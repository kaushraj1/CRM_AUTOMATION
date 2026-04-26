"""Retry with exponential backoff for transient errors."""

import time
from functools import wraps
from shared.logger import warn, error


def with_retry(max_attempts: int = 3, base_delay: float = 2.0):
    """Decorator: retry a function on exception with exponential backoff."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        warn(
                            f"{func.__name__} failed (attempt {attempt}/{max_attempts}), retrying in {delay}s",
                            error=str(e),
                        )
                        time.sleep(delay)
                    else:
                        error(f"{func.__name__} failed after {max_attempts} attempts", error=str(e))
            raise last_exception
        return wrapper
    return decorator
