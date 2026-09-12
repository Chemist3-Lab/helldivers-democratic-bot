"""Shared async retry decorator with exponential backoff.

See ARCHITECTURE.md §7.2 for the retry policy specification.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from httpx import HTTPStatusError

log = logging.getLogger(__name__)

T = TypeVar("T")


def async_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
) -> Callable[
    [Callable[..., Coroutine[Any, Any, T]]],
    Callable[..., Coroutine[Any, Any, T]],
]:
    """Decorator for async functions with exponential backoff retry.

    Args:
        max_retries: Maximum number of retry attempts after the initial call.
        base_delay: Initial delay in seconds before the first retry.
        backoff_factor: Multiplier applied to the delay on each subsequent retry.

    Retries on:
        - httpx.HTTPStatusError (5xx or 429)
        - asyncio.TimeoutError
        - OSError (connection errors)
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, T]],
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except HTTPStatusError as exc:
                    # Only retry on server errors and rate limits
                    if exc.response.status_code < 500 and exc.response.status_code != 429:
                        raise
                    last_exc = exc
                except (asyncio.TimeoutError, OSError) as exc:
                    last_exc = exc

                if attempt < max_retries:
                    delay = base_delay * (backoff_factor ** attempt)
                    log.warning(
                        "%s attempt %d/%d failed: %s. Retrying in %.1fs",
                        func.__name__,
                        attempt + 1,
                        max_retries,
                        last_exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

            # All retries exhausted
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
