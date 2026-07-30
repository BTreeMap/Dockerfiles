"""The retry algebra, pinned without running a single subprocess.

This loop used to exist twice, once per call site, which is why its edge cases
are worth stating explicitly: the attempt count a caller reports, and the rule
that no delay is served after the final failure. Both were previously provable
only by reading two copies and hoping they agreed.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable

import pytest

from ci.retry import Exhausted, Succeeded, backoff_seconds, with_retries

LOG = logging.getLogger("test.retry")


def _failing(times: int) -> tuple[list[int], Callable[[], None]]:
    """An operation that raises `times` times, then succeeds."""
    calls: list[int] = []

    def operation() -> None:
        calls.append(len(calls) + 1)
        if len(calls) <= times:
            raise subprocess.CalledProcessError(1, "docker")

    return calls, operation


# --- outcomes --------------------------------------------------------------


def test_first_attempt_success_reports_one_attempt() -> None:
    calls, operation = _failing(0)
    outcome = with_retries(operation, 5, "build", LOG, sleep=lambda _: None)
    assert outcome == Succeeded(attempts=1)
    assert calls == [1]


def test_transient_failure_is_retried_and_the_count_is_reported() -> None:
    calls, operation = _failing(2)
    outcome = with_retries(operation, 5, "build", LOG, sleep=lambda _: None)
    assert outcome == Succeeded(attempts=3)
    assert len(calls) == 3


def test_an_exhausted_budget_carries_the_last_error() -> None:
    _, operation = _failing(99)
    outcome = with_retries(operation, 3, "build", LOG, sleep=lambda _: None)
    assert isinstance(outcome, Exhausted)
    assert outcome.attempts == 3
    # Never None, and never the empty placeholder: an exhausted budget that
    # cannot say why is the failure mode this sum exists to prevent.
    assert "docker" in outcome.error


@pytest.mark.parametrize("budget", [0, -1])
def test_a_non_positive_budget_means_unlimited(budget: int) -> None:
    """The workflow's convention: an unset retry budget is an unbounded one."""
    calls, operation = _failing(7)
    outcome = with_retries(operation, budget, "build", LOG, sleep=lambda _: None)
    assert outcome == Succeeded(attempts=8)
    assert len(calls) == 8


def test_a_missing_executable_is_retried_like_any_other_failure() -> None:
    """OSError, not just a non-zero exit: the daemon may simply not be there."""

    def operation() -> None:
        raise FileNotFoundError("docker: command not found")

    outcome = with_retries(operation, 2, "build", LOG, sleep=lambda _: None)
    assert isinstance(outcome, Exhausted)
    assert outcome.attempts == 2


def test_an_unexpected_exception_is_not_swallowed() -> None:
    """Only the declared failure modes are retried; a defect must propagate."""

    def operation() -> None:
        raise ZeroDivisionError("a real bug")

    with pytest.raises(ZeroDivisionError):
        with_retries(operation, 3, "build", LOG, sleep=lambda _: None)


# --- the delay schedule ----------------------------------------------------


def test_no_delay_is_served_after_the_final_attempt() -> None:
    """Sleeping before returning failure adds the whole backoff to nothing.

    With a budget of 3 the operation is tried 3 times but waits only twice --
    between the attempts, never after the last one.
    """
    slept: list[float] = []
    _, operation = _failing(99)

    with_retries(
        operation, 3, "build", LOG, sleep=slept.append, backoff=lambda attempt: float(attempt)
    )
    assert slept == [1.0, 2.0]


def test_a_successful_attempt_serves_no_delay_at_all() -> None:
    slept: list[float] = []
    calls, operation = _failing(0)
    with_retries(operation, 3, "build", LOG, sleep=slept.append)
    assert slept == []


def test_backoff_is_capped_and_fully_jittered() -> None:
    # Full jitter draws over [0, cap]; both ends are asserted so that a change
    # to cap-only backoff -- which would retry every slot in lockstep -- fails.
    assert backoff_seconds(1, uniform=lambda _lo, hi: hi) == 1.0
    assert backoff_seconds(4, uniform=lambda _lo, hi: hi) == 8.0
    assert backoff_seconds(50, uniform=lambda _lo, hi: hi) == 60.0
    assert backoff_seconds(3, uniform=lambda lo, _hi: lo) == 0.0
