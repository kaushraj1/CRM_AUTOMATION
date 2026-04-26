"""Structured logging with automatic secret masking."""

import json
import os
from datetime import datetime, timezone


_SECRET_KEYS = [
    "EURI_API_KEY", "OPENROUTER_API_KEY",
    "HUBSPOT_API_KEY",
    "ZOHO_REFRESH_TOKEN", "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET",
    "AIRTABLE_API_KEY",
    "RESEND_API_KEY",
    "SLACK_WEBHOOK_URL",
]


def _mask_secrets(text: str) -> str:
    """Replace any env var values that look like secrets."""
    for key in _SECRET_KEYS:
        value = os.getenv(key, "")
        if value and len(value) > 4:
            masked = value[:4] + "*" * (len(value) - 4)
            text = text.replace(value, masked)
    return text


def log(level: str, message: str, **extra):
    """Print a structured JSON log line to stderr with secrets masked."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level.upper(),
        "message": _mask_secrets(str(message)),
    }
    if extra:
        entry["data"] = {k: _mask_secrets(str(v)) for k, v in extra.items()}
    import sys
    print(json.dumps(entry), file=sys.stderr)


def info(message: str, **extra):
    log("INFO", message, **extra)


def error(message: str, **extra):
    log("ERROR", message, **extra)


def warn(message: str, **extra):
    log("WARN", message, **extra)
