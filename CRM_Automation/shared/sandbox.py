"""Restrict file write paths to safe directories."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ALLOWED_DIRS = [PROJECT_ROOT / ".tmp", PROJECT_ROOT / "runs"]


def validate_write_path(path: str) -> Path:
    """Ensure path is inside an allowed directory. Raise if not."""
    target = Path(path).resolve()
    for allowed in ALLOWED_DIRS:
        allowed = allowed.resolve()
        if str(target).startswith(str(allowed)):
            return target
    raise PermissionError(
        f"Write blocked — {path} is outside allowed directories: {[str(d) for d in ALLOWED_DIRS]}"
    )
