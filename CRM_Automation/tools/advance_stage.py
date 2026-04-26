"""Advance lead stage based on band + first-touch sent.

Usage:
    python tools/advance_stage.py --source mock --input .tmp/routed_leads.json
    python tools/advance_stage.py --source airtable --input .tmp/routed_leads.json --dry-run

Stage rules:
    band=hot  + first email sent → stage=contacted
    band=warm + first email sent → stage=nurture
    band=cold                    → stage stays 'new'
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.env_loader import load_env
from shared.logger import info, error
from shared.sandbox import validate_write_path
from crm.factory import get_client


PROJECT_ROOT = Path(__file__).parent.parent


def _target_stage(lead: dict) -> str | None:
    band = (lead.get("band") or "").lower()
    if band == "hot":
        return "contacted"
    if band == "warm":
        return "nurture"
    return None  # cold stays put


def main():
    parser = argparse.ArgumentParser(description="Advance lead stages")
    parser.add_argument("--source", required=True, choices=["airtable", "hubspot", "zoho", "mock"])
    parser.add_argument("--input", default=".tmp/routed_leads.json")
    parser.add_argument("--output", default=".tmp/stage_receipts.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()

    input_path = PROJECT_ROOT / args.input
    if not input_path.exists():
        error(f"Input file not found: {input_path}")
        sys.exit(1)

    leads = json.loads(input_path.read_text())
    if not leads:
        info("No leads to advance")
        print(json.dumps({"status": "success", "advanced": 0, "skipped": 0}))
        return

    if args.dry_run:
        receipts = [
            {"id": l.get("id"), "from": l.get("stage"), "to": _target_stage(l), "status": "dry-run"}
            for l in leads
        ]
        out = validate_write_path(str(PROJECT_ROOT / args.output))
        out.write_text(json.dumps(receipts, indent=2))
        print(json.dumps({
            "status": "success",
            "mode": "dry-run",
            "would_advance": sum(1 for r in receipts if r["to"]),
            "output_path": str(out),
        }))
        return

    try:
        client = get_client(args.source)
    except (EnvironmentError, ValueError) as e:
        error(f"CRM init failed: {e}")
        sys.exit(2)

    receipts = []
    advanced = skipped = errors = 0

    for lead in leads:
        target = _target_stage(lead)
        if not target or target == lead.get("stage"):
            receipts.append({"id": lead.get("id"), "status": "skipped", "reason": "no transition"})
            skipped += 1
            continue
        res = client.advance_stage(lead["id"], target)
        receipts.append({"id": lead.get("id"), "to": target, **res})
        if res.get("status") == "updated":
            advanced += 1
        else:
            errors += 1

    out = validate_write_path(str(PROJECT_ROOT / args.output))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipts, indent=2))

    print(json.dumps({
        "status": "success",
        "source": args.source,
        "advanced": advanced,
        "skipped": skipped,
        "errors": errors,
        "output_path": str(out),
    }))


if __name__ == "__main__":
    main()
