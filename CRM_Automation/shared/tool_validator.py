"""Scan tool files for dangerous imports/functions before execution."""

import re
from pathlib import Path

BLOCKED_PATTERNS = [
    r'\bexec\s*\(',
    r'\beval\s*\(',
    r'\bos\.system\s*\(',
    r'\bos\.popen\s*\(',
    r'\b__import__\s*\(',
    r'\bsubprocess\.(call|run|Popen|check_output)\s*\(',
    r'shell\s*=\s*True',
]


def validate_tool(filepath: str) -> dict:
    """Scan a .py file for blocked patterns. Returns {"safe": bool, "issues": [...]}."""
    path = Path(filepath)
    if not path.exists():
        return {"safe": False, "issues": [f"File not found: {filepath}"]}
    if path.suffix != ".py":
        return {"safe": False, "issues": [f"Not a Python file: {filepath}"]}

    content = path.read_text()
    issues = []
    for pattern in BLOCKED_PATTERNS:
        matches = re.findall(pattern, content)
        if matches:
            issues.append(f"Blocked pattern found: {pattern} ({len(matches)} occurrence(s))")

    return {"safe": len(issues) == 0, "issues": issues}
