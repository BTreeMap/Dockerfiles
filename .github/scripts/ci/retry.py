"""Bounded retry with jittered backoff, written once.

Two call sites drive a subprocess that is allowed to fail transiently -- image
builds and manifest fusion -- and both had grown their own copy of the same
eleven-line loop: an `unlimited` flag, a `budget` string for the log, an attempt
counter, a `last_error` accumulator, and an easily-miscounted rule about not
sleeping after the final attempt. Two copies of a control-flow invariant is one
too many; the second is where the fix does not get made.

Retrying is only sound because the effects underneath are idempotent. A build
pushes identical content under identical tags on every attempt, and manifest
fusion is a pure function of tags that already exist, so a duplicate attempt
costs minutes and changes nothing observable. Do not reach for this to wrap an
effect that accumulates.

The outcome is a closed sum rather than a bool-and-string pair, so "succeeded but
carries an error" is not a state a caller can be handed.
"""

from __future__ import annotations

import logging
import random
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Succeeded:
    attempts: int


@dataclass(frozen=True, slots=True)
class Exhausted:
    """Every attempt in the budget failed. Carries the last error, never None."""

    attempts: int
    error: str


RetryOutcome = Succeeded | Exhausted


def backoff_seconds(
    attempt: int,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 60.0,
    uniform: Callable[[float, float], float] | None = None,
) -> float:
    """Capped exponential backoff with full jitter.

    Full jitter -- uniform over [0, cap] rather than cap itself -- is what stops
    a whole worker's slots retrying in lockstep after a shared registry blip.

    `uniform` is injectable so the schedule can be tested at both ends of the
    draw without a seeded global. It was previously typed `object` and probed
    with `callable()`, which defeated the checker at exactly the point where a
    wrong argument would otherwise be caught.
    """
    draw = random.uniform if uniform is None else uniform
    cap = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
    return float(draw(0.0, cap))


# Both call sites shell out, so a failure arrives either as a non-zero exit
# (CalledProcessError) or as the process never starting at all (OSError).
_SUBPROCESS_FAILURES: tuple[type[BaseException], ...] = (subprocess.CalledProcessError, OSError)


def with_retries(
    operation: Callable[[], None],
    max_retries: int,
    label: str,
    log: logging.Logger,
    sleep: Callable[[float], None] = time.sleep,
    backoff: Callable[[int], float] = backoff_seconds,
    retry_on: tuple[type[BaseException], ...] = _SUBPROCESS_FAILURES,
) -> RetryOutcome:
    """Runs `operation` until it returns without raising, or the budget is spent.

    `max_retries <= 0` means unlimited, matching the workflow's convention that
    an unset budget is an unbounded one. Attempts are numbered from 1, and no
    delay is served after the final attempt -- sleeping before returning failure
    would add the whole backoff to the critical path for nothing.

    Logging lives here rather than in the caller because the attempt counter is
    here: it is the effect boundary, and burying instrumentation one level down
    from where the retry actually happens is what makes an exhausted budget look
    like a single failure in the log.
    """
    unlimited = max_retries <= 0
    budget = "∞" if unlimited else str(max_retries)
    attempt = 0
    last_error = "no attempt was made"

    while unlimited or attempt < max_retries:
        attempt += 1
        log.info("%s (attempt %d/%s)", label, attempt, budget)
        try:
            operation()
            return Succeeded(attempts=attempt)
        except retry_on as error:
            last_error = str(error)
            log.warning("Attempt %d/%s failed for %s: %s", attempt, budget, label, error)

        if not unlimited and attempt >= max_retries:
            break
        sleep(backoff(attempt))

    log.error("All %d attempt(s) failed for %s", attempt, label)
    return Exhausted(attempts=attempt, error=last_error)
