"""Fetch new leads from a CRM since the last successful run.

Usage:
    python tools/fetch_leads.py --source mock --output .tmp/new_leads.json
    python tools/fetch_leads.py --source airtable --since-state .tmp/last_run.json
    python tools/fetch_leads.py --source hubspot --since 2026-04-15

Output:
    Writes JSON array of normalized lead dicts to --output and prints summary to stdout.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.env_loader import load_env
from shared.logger import info, error, warn
from shared.sandbox import validate_write_path
from crm.factory import get_client


PROJECT_ROOT = Path(__file__).parent.parent
DEFAULT_STATE_PATH = PROJECT_ROOT / ".tmp" / "last_run.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / ".tmp" / "new_leads.json"


def _load_state(state_path: Path, source: str) -> tuple[datetime, set[str]]:
    """Return (last_run_at, processed_ids_for_source). Defaults to T-7d, empty set."""
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text())
            last_run_iso = data.get("last_run_at")
            if last_run_iso:
                last_run = datetime.fromisoformat(last_run_iso.replace("Z", "+00:00"))
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=timezone.utc)
                processed = set(data.get("processed_lead_ids", {}).get(source, []))
                return last_run, processed
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            warn(f"State file malformed, defaulting to T-7d: {e}")

    return datetime.now(timezone.utc) - timedelta(days=7), set()


def main():
    parser = argparse.ArgumentParser(description="Fetch new leads from a CRM")
    parser.add_argument("--source", required=True, choices=["airtable", "hubspot", "zoho", "mock"])
    parser.add_argument("--since-state", default=str(DEFAULT_STATE_PATH),
                        help="Path to last_run.json (default: .tmp/last_run.json)")
    parser.add_argument("--since", default=None,
                        help="Override since-state with explicit ISO date (YYYY-MM-DD)")
    parser.add_argument("--max-leads", type=int, default=50)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    args = parser.parse_args()

    load_env()

    state_path = Path(args.since_state)
    last_run, processed_ids = _load_state(state_path, args.source)

    if args.since:
        try:
            last_run = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            error(f"Invalid --since value: {args.since}. Use YYYY-MM-DD.")
            sys.exit(1)

    info(f"Fetching {args.source} leads since {last_run.isoformat()}")

    try:
        client = get_client(args.source)
        leads = client.fetch_leads(since=last_run, limit=args.max_leads)
    except EnvironmentError as e:
        error(f"Auth/config error: {e}")
        sys.exit(2)
    except Exception as e:
        error(f"fetch_leads failed: {type(e).__name__}: {e}")
        sys.exit(3)

    # Defensive idempotency: filter out IDs we've already processed
    fresh = [l for l in leads if l.get("id") not in processed_ids]
    skipped = len(leads) - len(fresh)
    if skipped:
        info(f"Skipped {skipped} already-processed leads (idempotency)")

    output_path = validate_write_path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fresh, indent=2, ensure_ascii=False))

    print(json.dumps({
        "status": "success",
        "source": args.source,
        "since": last_run.isoformat(),
        "fetched": len(leads),
        "skipped_already_processed": skipped,
        "new": len(fresh),
        "output_path": str(output_path),
    }))


if __name__ == "__main__":
    main()
