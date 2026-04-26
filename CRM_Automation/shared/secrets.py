"""Mask secrets in any string before logging or displaying."""

import os


_SECRET_KEYS = [
    "EURI_API_KEY", "OPENROUTER_API_KEY",
    "HUBSPOT_API_KEY",
    "ZOHO_REFRESH_TOKEN", "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET",
    "AIRTABLE_API_KEY",
    "RESEND_API_KEY",
    "SLACK_WEBHOOK_URL",
]


def mask(text: str) -> str:
    """Replace known secret values with masked versions."""
    for key in _SECRET_KEYS:
        value = os.getenv(key, "")
        if value and len(value) > 4:
            masked = value[:4] + "*" * (len(value) - 4)
            text = text.replace(value, masked)
    return text
