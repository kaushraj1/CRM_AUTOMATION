"""Load and validate environment variables from .env file."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv


def load_env():
    """Load .env from project root. Warns (not exits) if missing — tools enforce
    required-ness via `get_required()`. Mock backend works with no .env at all.
    """
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    elif not os.getenv("CI") and not os.getenv("GITHUB_ACTIONS"):
        print(
            "WARNING: .env file not found. OS env vars only. "
            "Copy .env.example to .env if you need API keys.",
            file=sys.stderr,
        )


def get_required(key: str) -> str:
    """Get a required env var. Exit if missing."""
    value = os.getenv(key, "").strip()
    if not value:
        print(f"ERROR: {key} is required but not set in .env")
        sys.exit(1)
    return value


def get_optional(key: str, default: str = "") -> str:
    """Get an optional env var with a default."""
    return os.getenv(key, default).strip()
