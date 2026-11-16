"""Shared, sanitized control signals for bounded Phase 3 collection."""

from __future__ import annotations

import math
import time


class CollectionBudgetExceeded(RuntimeError):
    """A source request was refused before issuance by the durable budget."""


class CollectionDeadlineExceeded(RuntimeError):
    """The configured build wall-time expired before more source work."""


def remaining_timeout_seconds(
    deadline_monotonic: float | None,
    navigation_timeout_seconds: int,
) -> int:
    """Return a positive timeout capped by both navigation and build limits."""
    if deadline_monotonic is None:
        return navigation_timeout_seconds
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise CollectionDeadlineExceeded("phase3_collection_deadline_exceeded")
    return max(1, min(navigation_timeout_seconds, math.ceil(remaining)))


def remaining_timeout_milliseconds(
    deadline_monotonic: float | None,
    navigation_timeout_seconds: int,
) -> int:
    """Return a non-rounded millisecond allowance for cancellable browser waits."""
    maximum = navigation_timeout_seconds * 1000
    if deadline_monotonic is None:
        return maximum
    remaining = math.floor((deadline_monotonic - time.monotonic()) * 1000)
    if remaining <= 0:
        raise CollectionDeadlineExceeded("phase3_collection_deadline_exceeded")
    return min(maximum, remaining)


def deadline_aware_sleep(
    requested_seconds: float,
    deadline_monotonic: float | None,
) -> None:
    """Sleep at most to the absolute deadline and fail when the delay is cut short."""
    if requested_seconds <= 0:
        return
    if deadline_monotonic is None:
        time.sleep(requested_seconds)
        return
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise CollectionDeadlineExceeded("phase3_collection_deadline_exceeded")
    actual = min(requested_seconds, remaining)
    time.sleep(actual)
    if actual < requested_seconds or time.monotonic() >= deadline_monotonic:
        raise CollectionDeadlineExceeded("phase3_collection_deadline_exceeded")
