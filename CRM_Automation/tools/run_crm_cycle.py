"""Orchestrator — runs one full lead-lifecycle cycle.

Steps (per workflows/lead-lifecycle.md):
    1. fetch_leads     → .tmp/new_leads.json
    2. score_lead      → .tmp/scored_leads.json
    3. route_lead      → .tmp/routed_leads.json
    4. update_crm      → write fields + create tasks
    5. send_followup   → first-touch email
    6. advance_stage   → push stage forward
    7. persist last_run.json (idempotency state)
    8. write run log to runs/YYYY-MM-DD-lead-lifecycle.md

Usage:
    python tools/run_crm_cycle.py --source mock --dry-run
    python tools/run_crm_cycle.py --source airtable --max-leads 10
    python tools/run_crm_cycle.py --source mock --no-llm --dry-run

Idempotency: respects .tmp/last_run.json. Re-running within the same window is a no-op
because processed lead IDs are filtered in step 1.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.env_loader import load_env
from shared.logger import info, error, warn
from shared.sandbox import validate_write_path

# Import sibling tools as modules so we don't shell out
from tools import fetch_leads as t_fetch
from tools import score_lead as t_score
from tools import route_lead as t_route
from tools import update_crm as t_update
from tools import send_followup as t_send
from tools import advance_stage as t_stage


PROJECT_ROOT = Path(__file__).parent.parent
STATE_PATH = PROJECT_ROOT / ".tmp" / "last_run.json"


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return {"last_run_at": None, "processed_lead_ids": {}, "last_run_summary": {}}
    return {"last_run_at": None, "processed_lead_ids": {}, "last_run_summary": {}}


def _save_state(state: dict):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _run_step(name: str, argv: list[str], runner) -> dict:
    """Invoke a sibling tool's main() with synthetic argv. Capture its stdout JSON."""
    import io
    import contextlib

    saved_argv = sys.argv
    sys.argv = [name] + argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            runner.main()
    except SystemExit as e:
        if e.code not in (None, 0):
            error(f"Step {name} exited with code {e.code}")
            return {"status": "error", "step": name, "exit_code": e.code}
    finally:
        sys.argv = saved_argv

    out = buf.getvalue().strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else {}
    except (json.JSONDecodeError, IndexError):
        warn(f"Step {name} produced non-JSON output: {out[:200]}")
        return {"status": "ok", "raw": out[:200]}


def main():
    parser = argparse.ArgumentParser(description="Run one full CRM lifecycle cycle")
    parser.add_argument("--source", required=True, choices=["airtable", "hubspot", "zoho", "mock"])
    parser.add_argument("--dry-run", action="store_true", help="No writes to CRM, no emails sent")
    parser.add_argument("--max-leads", type=int, default=50)
    parser.add_argument("--no-llm", action="store_true", help="Use heuristic scoring only")
    parser.add_argument("--skip-tasks", action="store_true", help="Don't create owner tasks")
    parser.add_argument("--skip-emails", action="store_true", help="Don't send first-touch emails")
    args = parser.parse_args()

    load_env()
    started_at = datetime.now(timezone.utc)
    info(f"=== Cycle start: source={args.source} dry_run={args.dry_run} ===")

    state = _load_state()
    summary: dict = {
        "started_at": started_at.isoformat(),
        "source": args.source,
        "dry_run": args.dry_run,
        "steps": {},
    }

    # ── Step 1: fetch ───────────────────────────────────────────────────
    fetch_argv = [
        "--source", args.source,
        "--since-state", str(STATE_PATH),
        "--max-leads", str(args.max_leads),
        "--output", ".tmp/new_leads.json",
    ]
    fetch_result = _run_step("fetch_leads", fetch_argv, t_fetch)
    summary["steps"]["fetch"] = fetch_result
    new_count = fetch_result.get("new", 0)
    if new_count == 0:
        info("No new leads — cycle ends early")
        summary["leads_processed"] = 0
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        # advance state timestamp anyway so next run window is correct
        state["last_run_at"] = started_at.isoformat()
        state["last_run_summary"] = summary
        _save_state(state)
        print(json.dumps({"status": "success", "leads_processed": 0,
                          "hot": 0, "warm": 0, "cold": 0, "summary": summary}))
        return

    # ── Step 2: score ───────────────────────────────────────────────────
    score_argv = ["--input", ".tmp/new_leads.json", "--output", ".tmp/scored_leads.json"]
    if args.no_llm:
        score_argv.append("--no-llm")
    score_result = _run_step("score_lead", score_argv, t_score)
    summary["steps"]["score"] = score_result

    # ── Step 3: route ───────────────────────────────────────────────────
    route_result = _run_step(
        "route_lead",
        ["--input", ".tmp/scored_leads.json", "--output", ".tmp/routed_leads.json"],
        t_route,
    )
    summary["steps"]["route"] = route_result

    # ── Step 4: update CRM ──────────────────────────────────────────────
    update_argv = ["--source", args.source, "--input", ".tmp/routed_leads.json"]
    if args.dry_run:
        update_argv.append("--dry-run")
    if not args.skip_tasks:
        update_argv.append("--create-tasks")
    update_result = _run_step("update_crm", update_argv, t_update)
    summary["steps"]["update"] = update_result

    # ── Step 5: send first-touch ────────────────────────────────────────
    if not args.skip_emails:
        send_argv = ["--input", ".tmp/routed_leads.json", "--touch", "first"]
        if args.dry_run:
            send_argv.append("--dry-run")
        send_result = _run_step("send_followup", send_argv, t_send)
        summary["steps"]["send"] = send_result

    # ── Step 6: advance stage ───────────────────────────────────────────
    stage_argv = ["--source", args.source, "--input", ".tmp/routed_leads.json"]
    if args.dry_run:
        stage_argv.append("--dry-run")
    stage_result = _run_step("advance_stage", stage_argv, t_stage)
    summary["steps"]["stage"] = stage_result

    # ── Step 7-8: persist state + run log ───────────────────────────────
    routed_path = PROJECT_ROOT / ".tmp" / "routed_leads.json"
    processed_ids: list[str] = []
    band_counts = {"hot": 0, "warm": 0, "cold": 0}
    if routed_path.exists():
        leads = json.loads(routed_path.read_text())
        processed_ids = [l.get("id") for l in leads if l.get("id")]
        for l in leads:
            band = (l.get("band") or "cold").lower()
            band_counts[band] = band_counts.get(band, 0) + 1

    if not args.dry_run:
        state["last_run_at"] = started_at.isoformat()
        state.setdefault("processed_lead_ids", {}).setdefault(args.source, [])
        existing = set(state["processed_lead_ids"][args.source])
        for lid in processed_ids:
            existing.add(lid)
        state["processed_lead_ids"][args.source] = list(existing)
    else:
        info("DRY-RUN: state file NOT advanced")

    summary["leads_processed"] = len(processed_ids)
    summary["band_counts"] = band_counts
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    state["last_run_summary"] = summary
    _save_state(state)

    # Run log
    log_name = f"{started_at.strftime('%Y-%m-%d-%H%M%S')}-lead-lifecycle.md"
    log_path = validate_write_path(str(PROJECT_ROOT / "runs" / log_name))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(_format_run_log(summary))

    final = {
        "status": "success",
        "source": args.source,
        "dry_run": args.dry_run,
        "leads_processed": summary["leads_processed"],
        "hot": band_counts["hot"],
        "warm": band_counts["warm"],
        "cold": band_counts["cold"],
        "log_path": str(log_path),
    }
    info(f"=== Cycle done: {final} ===")
    print(json.dumps(final))


def _format_run_log(summary: dict) -> str:
    lines = [
        f"# Lead Lifecycle Run — {summary['started_at']}",
        "",
        f"- Source: `{summary['source']}`",
        f"- Dry run: `{summary['dry_run']}`",
        f"- Leads processed: {summary.get('leads_processed', 0)}",
        f"- Bands: {summary.get('band_counts', {})}",
        f"- Finished at: {summary.get('finished_at')}",
        "",
        "## Steps",
        "",
    ]
    for name, result in summary.get("steps", {}).items():
        lines.append(f"### {name}")
        lines.append(f"```json\n{json.dumps(result, indent=2)}\n```")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
