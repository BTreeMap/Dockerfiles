"""Measuring how much of a runner a worker actually used.

Slot count was inherited as cpu_count() from the original process pool, and a
Docker build spends most of its life waiting on the network rather than on a
core -- so the right number is an empirical question, not an architectural one.
These functions turn a run's build intervals into the two numbers that answer
it, and both are pure so they can be tested without building anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

Interval = tuple[float, float]


def intervals_of(builds: Iterable[tuple[float, float]]) -> tuple[Interval, ...]:
    """Normalises (start, duration) pairs into (start, end), dropping empties."""
    return tuple(
        (start, start + duration) for start, duration in builds if duration > 0.0
    )


def busy_seconds(intervals: Sequence[Interval]) -> float:
    """Wall-clock time during which at least one build was running.

    The union of the intervals, not their sum: overlapping builds must be
    counted once, or a worker running four at a time would appear to have been
    busy four times longer than the clock allows.
    """
    if not intervals:
        return 0.0

    total = 0.0
    current_start, current_end = min(intervals)
    for start, end in sorted(intervals)[1:]:
        if start > current_end:
            total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    return total + (current_end - current_start)


def effective_parallelism(intervals: Sequence[Interval]) -> float:
    """Average number of builds in flight while the worker was busy.

    Compare against the configured slot count. Materially below it means slots
    sat idle -- the worker ran out of work, not out of capacity. At or near it
    means the slots were saturated and raising the count is worth testing.
    """
    busy = busy_seconds(intervals)
    if busy <= 0.0:
        return 0.0
    return sum(end - start for start, end in intervals) / busy


def peak_concurrency(intervals: Sequence[Interval]) -> int:
    """The most builds ever running at once, by sweep over interval endpoints."""
    if not intervals:
        return 0

    # +1 at every start, -1 at every end; ends sort before starts at equal times
    # so a build that finishes exactly as another begins is not double-counted.
    events = sorted(
        [(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals],
        key=lambda event: (event[0], event[1]),
    )
    running = peak = 0
    for _, delta in events:
        running += delta
        peak = max(peak, running)
    return peak
