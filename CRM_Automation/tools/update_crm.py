"""Write score, owner, track, stage back to the CRM.

Usage:
    python tools/update_crm.py --source mock --input .tmp/routed_leads.json
    python tools/update_crm.py --source airtable --input .tmp/routed_leads.json --dry-run
    python tools/update_crm.py --source mock --input .tmp/routed_leads.json --create-tasks

In --dry-run mode, NO writes happen. Tool reports what it WOULD do.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.env_loader import load_env
from shared.logger import info, warn, error
from shared.sandbox import validate_write_path
from crm.factory import get_client


PROJECT_ROOT = Path(__file__).parent.parent
WRITABLE_FIELDS = {"score", "band", "owner_email", "track"}  # `stage` handled by advance_stage.py


def main():
    parser = argparse.ArgumentParser(description="Update CRM with scored+routed lead fields")
    parser.add_argument("--source", required=True, choices=["airtable", "hubspot", "zoho", "mock"])
    parser.add_argument("--input", default=".tmp/routed_leads.json")
    parser.add_argument("--output", default=".tmp/update_receipts.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--create-tasks", action="store_true",
                        help="Also create owner tasks for hot/warm leads")
    args = parser.parse_args()

    load_env()

    input_path = PROJECT_ROOT / args.input
    if not input_path.exists():
        error(f"Input file not found: {input_path}")
        sys.exit(1)

    leads = json.loads(input_path.read_text())
    if not leads:
        info("No leads to update")
        print(json.dumps({"status": "success", "updated": 0, "skipped": 0, "errors": 0}))
        return

    if args.dry_run:
        info(f"DRY-RUN: would update {len(leads)} leads in {args.source}")
        receipts = [
            {
                "id": l.get("id"),
                "status": "dry-run",
                "would_set": {k: l.get(k) for k in WRITABLE_FIELDS if l.get(k) is not None},
                "would_create_task": bool(args.create_tasks and l.get("band") in ("hot", "warm")),
            }
            for l in leads
        ]
        out = validate_write_path(str(PROJECT_ROOT / args.output))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(receipts, indent=2))
        print(json.dumps({
            "status": "success",
            "mode": "dry-run",
            "would_update": len(leads),
            "would_create_tasks": sum(1 for r in receipts if r["would_create_task"]),
            "output_path": str(out),
        }))
        return

    try:
        client = get_client(args.source)
    except (EnvironmentError, ValueError) as e:
        error(f"CRM init failed: {e}")
        sys.exit(2)

    receipts = []
    updated = skipped = errors = tasks_created = 0

    import yaml
    tracks_cfg = {}
    tracks_yaml = PROJECT_ROOT / "config" / "tracks.yaml"
    if tracks_yaml.exists():
        tracks_cfg = (yaml.safe_load(tracks_yaml.read_text()) or {}).get("tracks", {})

    for lead in leads:
        update_fields = {k: lead.get(k) for k in WRITABLE_FIELDS if lead.get(k) is not None}
        receipt = {"id": lead.get("id"), "lead_email": lead.get("email")}

        if not update_fields:
            receipt["update"] = {"status": "skipped", "message": "no fields to update"}
            skipped += 1
        else:
            res = client.update_lead(lead["id"], update_fields)
            receipt["update"] = res
            if res.get("status") == "updated":
                updated += 1
            elif res.get("status") == "skipped":
                skipped += 1
            else:
                errors += 1

        # Optionally create owner task
        if args.create_tasks and lead.get("band") in ("hot", "warm") and lead.get("owner_email"):
            track_cfg = tracks_cfg.get(lead.get("track") or "", {})
            task_title = (track_cfg.get("owner_task") or f"Follow up with {lead.get('name')}")
            task_title = task_title.replace("{{name}}", lead.get("name") or "lead") \
                                   .replace("{{company}}", lead.get("company") or "")
            due_iso = lead.get("next_touch_at") or datetime.now(timezone.utc).isoformat()
            task_res = client.create_task(
                lead_id=lead["id"],
                owner_email=lead["owner_email"],
                title=task_title,
                due_at_iso=due_iso,
            )
            receipt["task"] = task_res
            if task_res.get("status") == "created":
                tasks_created += 1

        receipts.append(receipt)

    out = validate_write_path(str(PROJECT_ROOT / args.output))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipts, indent=2))

    print(json.dumps({
        "status": "success",
        "source": args.source,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "tasks_created": tasks_created,
        "output_path": str(out),
    }))


if __name__ == "__main__":
    main()
