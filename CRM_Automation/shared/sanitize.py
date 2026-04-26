"""Input sanitization — clean user inputs before passing to tools."""

import re


def sanitize_text(text: str) -> str:
    """Remove shell metacharacters and control characters."""
    cleaned = re.sub(r'[;&|`$(){}]', '', text)
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', cleaned)
    return cleaned.strip()


def sanitize_email(email: str) -> str:
    """Basic email format validation."""
    email = email.strip().lower()
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        raise ValueError(f"Invalid email format: {email}")
    return email


def sanitize_url(url: str) -> str:
    """Only allow http/https URLs. Block internal IPs."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"Invalid URL scheme: {url}")
    blocked = ["127.0.0.1", "localhost", "0.0.0.0", "169.254.", "10.", "192.168."]
    for pattern in blocked:
        if pattern in url:
            raise ValueError(f"Blocked internal URL: {url}")
    return url
