"""Send first-touch (or scheduled) follow-up emails per track.

Usage:
    python tools/send_followup.py --input .tmp/routed_leads.json --touch first
    python tools/send_followup.py --input .tmp/routed_leads.json --touch first --dry-run

Templates from config/tracks.yaml. Sends via Resend (RESEND_API_KEY).
In --dry-run mode, prints what would be sent without hitting Resend.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.env_loader import load_env, get_optional, get_required
from shared.logger import info, warn, error
from shared.sandbox import validate_write_path


PROJECT_ROOT = Path(__file__).parent.parent
TRACKS_YAML = PROJECT_ROOT / "config" / "tracks.yaml"
RESEND_URL = "https://api.resend.com/emails"


def _render(template: str, lead: dict) -> str:
    owner_email = lead.get("owner_email") or ""
    owner_first = owner_email.split("@")[0].split(".")[0].title() if owner_email else "The Team"

    replacements = {
        "{{name}}": lead.get("name") or "there",
        "{{first_name}}": (lead.get("name") or "there").split()[0],
        "{{company}}": lead.get("company") or "your team",
        "{{owner_first_name}}": owner_first,
        "{{owner_email}}": owner_email,
        "{{owner_calendar_url}}": f"https://cal.com/{owner_email.split('@')[0] if owner_email else 'team'}",
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace(key, value)
    return rendered


def _send_via_resend(api_key: str, sender: str, to: str, subject: str, body: str) -> dict:
    try:
        r = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": sender, "to": [to], "subject": subject, "text": body},
            timeout=15,
        )
        if not r.ok:
            return {"status": "error", "code": r.status_code, "message": r.text[:300]}
        data = r.json()
        return {"status": "sent", "message_id": data.get("id", "")}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Send follow-up emails per track")
    parser.add_argument("--input", default=".tmp/routed_leads.json")
    parser.add_argument("--output", default=".tmp/email_receipts.json")
    parser.add_argument("--touch", default="first",
                        help="Which touch to send: 'first' (index 0) or numeric 1..n")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env()

    input_path = PROJECT_ROOT / args.input
    if not input_path.exists():
        error(f"Input file not found: {input_path}")
        sys.exit(1)

    leads = json.loads(input_path.read_text())
    if not leads:
        info("No leads to email")
        print(json.dumps({"status": "success", "sent": 0, "skipped": 0, "errors": 0}))
        return

    if not TRACKS_YAML.exists():
        error("config/tracks.yaml is required")
        sys.exit(1)
    tracks_cfg = (yaml.safe_load(TRACKS_YAML.read_text()) or {}).get("tracks", {})

    touch_idx = 0 if args.touch == "first" else max(0, int(args.touch) - 1)

    api_key = get_optional("RESEND_API_KEY")
    sender = get_optional("EMAIL_FROM", "onboarding@resend.dev")

    if not args.dry_run and not api_key:
        warn("RESEND_API_KEY not set — forcing --dry-run")
        args.dry_run = True

    receipts = []
    sent = skipped = errors = 0

    for lead in leads:
        track_name = lead.get("track")
        track_cfg = tracks_cfg.get(track_name)
        receipt = {"id": lead.get("id"), "to": lead.get("email"), "track": track_name}

        if lead.get("do_not_contact"):
            receipt["status"] = "skipped"
            receipt["reason"] = "do_not_contact"
            skipped += 1
            receipts.append(receipt)
            continue

        if not lead.get("email"):
            receipt["status"] = "skipped"
            receipt["reason"] = "no_email"
            skipped += 1
            receipts.append(receipt)
            continue

        if not track_cfg or touch_idx >= len(track_cfg.get("touches", [])):
            receipt["status"] = "skipped"
            receipt["reason"] = f"no_touch_{touch_idx}_for_track_{track_name}"
            skipped += 1
            receipts.append(receipt)
            continue

        touch = track_cfg["touches"][touch_idx]
        subject = _render(touch.get("subject", ""), lead)
        body = _render(touch.get("body", ""), lead)

        if args.dry_run:
            receipt["status"] = "dry-run"
            receipt["subject"] = subject
            receipt["body_preview"] = body[:200]
            sent += 1
        else:
            result = _send_via_resend(api_key, sender, lead["email"], subject, body)
            receipt.update(result)
            if result["status"] == "sent":
                sent += 1
            else:
                errors += 1

        receipts.append(receipt)

    out = validate_write_path(str(PROJECT_ROOT / args.output))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipts, indent=2))

    print(json.dumps({
        "status": "success",
        "mode": "dry-run" if args.dry_run else "live",
        "sent": sent,
        "skipped": skipped,
        "errors": errors,
        "output_path": str(out),
    }))


if __name__ == "__main__":
    main()
