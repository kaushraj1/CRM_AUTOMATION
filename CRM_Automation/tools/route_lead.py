"""Route scored leads to owner + track based on band.

Usage:
    python tools/route_lead.py --input .tmp/scored_leads.json --output .tmp/routed_leads.json

Routing rules:
    band == "hot"  → first_call_24h, owner from round-robin (tracks: hot)
    band == "warm" → nurture_5_email, owner from round-robin (tracks: warm)
    band == "cold" → long_drip_monthly, no human owner

Owner pool from config/owners.yaml. Round-robin pointer in .tmp/owner_pointer.json.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.env_loader import load_env
from shared.logger import info, warn, error
from shared.sandbox import validate_write_path


PROJECT_ROOT = Path(__file__).parent.parent
OWNERS_YAML = PROJECT_ROOT / "config" / "owners.yaml"
TRACKS_YAML = PROJECT_ROOT / "config" / "tracks.yaml"
POINTER_FILE = PROJECT_ROOT / ".tmp" / "owner_pointer.json"


BAND_TO_TRACK = {
    "hot": "first_call_24h",
    "warm": "nurture_5_email",
    "cold": "long_drip_monthly",
}


def _load_owners() -> tuple[list[dict], int, str]:
    if not OWNERS_YAML.exists():
        warn("config/owners.yaml missing — using fallback")
        return [], 50, "nurture@example.com"
    cfg = yaml.safe_load(OWNERS_YAML.read_text()) or {}
    return (
        cfg.get("owners", []),
        int(cfg.get("capacity_limit", 50)),
        cfg.get("fallback_owner_email", "nurture@example.com"),
    )


def _load_tracks() -> dict:
    if not TRACKS_YAML.exists():
        return {}
    cfg = yaml.safe_load(TRACKS_YAML.read_text()) or {}
    return cfg.get("tracks", {})


def _load_pointer() -> dict:
    if POINTER_FILE.exists():
        try:
            return json.loads(POINTER_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_pointer(pointer: dict):
    POINTER_FILE.parent.mkdir(exist_ok=True)
    POINTER_FILE.write_text(json.dumps(pointer, indent=2))


def _next_owner_for_band(band: str, owners: list[dict], pointer: dict, fallback: str) -> str | None:
    """Pick next eligible owner round-robin for the given band."""
    eligible = [o for o in owners if band in (o.get("tracks") or [])]
    if not eligible:
        return fallback if band != "cold" else None  # cold has no human owner

    idx = pointer.get(band, 0)
    chosen = eligible[idx % len(eligible)]
    pointer[band] = (idx + 1) % len(eligible)
    return chosen.get("email") or fallback


def main():
    parser = argparse.ArgumentParser(description="Route scored leads → owner + track")
    parser.add_argument("--input", default=".tmp/scored_leads.json")
    parser.add_argument("--output", default=".tmp/routed_leads.json")
    args = parser.parse_args()

    load_env()

    input_path = PROJECT_ROOT / args.input
    if not input_path.exists():
        error(f"Input file not found: {input_path}")
        sys.exit(1)

    leads = json.loads(input_path.read_text())
    if not leads:
        info("No leads to route — writing empty output")
        validate_write_path(str(PROJECT_ROOT / args.output)).write_text("[]")
        print(json.dumps({"status": "success", "routed": 0}))
        return

    owners, _capacity, fallback = _load_owners()
    tracks = _load_tracks()
    pointer = _load_pointer()

    counts_by_track: dict[str, int] = {}
    counts_by_owner: dict[str, int] = {}

    now = datetime.now(timezone.utc)

    for lead in leads:
        band = (lead.get("band") or "cold").lower()
        track = BAND_TO_TRACK.get(band, "long_drip_monthly")
        owner_email = _next_owner_for_band(band, owners, pointer, fallback)

        lead["track"] = track
        lead["owner_email"] = owner_email

        track_cfg = tracks.get(track, {})
        due_in_h = track_cfg.get("task_due_in_hours")
        if due_in_h is not None and owner_email:
            lead["next_touch_at"] = (now + timedelta(hours=int(due_in_h))).isoformat()
        else:
            lead["next_touch_at"] = None

        # Stage transition rule: hot → contacted (after first email), warm → nurture, cold → new
        if band == "hot":
            lead["next_stage"] = "contacted"
        elif band == "warm":
            lead["next_stage"] = "nurture"
        else:
            lead["next_stage"] = "new"

        counts_by_track[track] = counts_by_track.get(track, 0) + 1
        if owner_email:
            counts_by_owner[owner_email] = counts_by_owner.get(owner_email, 0) + 1

    _save_pointer(pointer)

    output_path = validate_write_path(str(PROJECT_ROOT / args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(leads, indent=2, ensure_ascii=False))

    print(json.dumps({
        "status": "success",
        "routed": len(leads),
        "by_track": counts_by_track,
        "by_owner": counts_by_owner,
        "output_path": str(output_path),
    }))


if __name__ == "__main__":
    main()
