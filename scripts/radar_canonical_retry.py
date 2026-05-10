#!/usr/bin/env python3
"""Retry helpers for transient canonical substrate lock/contention failures."""

from __future__ import annotations

import time
from typing import Any, Callable, TypeVar


T = TypeVar("T")

_RETRYABLE_HINTS = (
    "database is locked",
    "conflicting lock",
    "lock",
    "resource busy",
    "resource temporarily unavailable",
    "io error",
    "i/o error",
    "cannot open file",
    "another process",
    "timeout",
)


def is_retryable_canonical_error(exc: Exception) -> bool:
    type_name = type(exc).__name__.lower()
    if "ioexception" in type_name or "operationalerror" in type_name:
        return True
    text = str(exc).strip().lower()
    return any(hint in text for hint in _RETRYABLE_HINTS)


def call_with_canonical_retries(
    func: Callable[[], T],
    *,
    attempts: int = 4,
    base_sleep_seconds: float = 0.75,
    max_sleep_seconds: float = 4.0,
) -> tuple[T, int]:
    """Run canonical DB operation with short backoff for transient lock/contention failures."""
    retry_count = 0
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return func(), retry_count
        except Exception as exc:
            if attempt >= attempts or not is_retryable_canonical_error(exc):
                raise
            retry_count += 1
            sleep_seconds = min(max_sleep_seconds, base_sleep_seconds * (2 ** (attempt - 1)))
            time.sleep(sleep_seconds)
    raise RuntimeError("unreachable canonical retry branch")
