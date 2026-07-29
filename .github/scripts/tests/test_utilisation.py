"""Utilisation arithmetic: the numbers BUILD_SLOTS should be tuned against."""

from __future__ import annotations

from ci.utilisation import busy_seconds, effective_parallelism, intervals_of, peak_concurrency


def test_no_builds_measures_nothing_rather_than_dividing_by_zero() -> None:
    assert busy_seconds(()) == 0.0
    assert effective_parallelism(()) == 0.0
    assert peak_concurrency(()) == 0


def test_sequential_builds_show_parallelism_of_one() -> None:
    spans = intervals_of([(0.0, 10.0), (10.0, 10.0), (20.0, 10.0)])
    assert busy_seconds(spans) == 30.0
    assert effective_parallelism(spans) == 1.0
    assert peak_concurrency(spans) == 1


def test_fully_overlapping_builds_show_the_slot_count() -> None:
    spans = intervals_of([(0.0, 10.0)] * 4)
    assert busy_seconds(spans) == 10.0     # union, not sum
    assert effective_parallelism(spans) == 4.0
    assert peak_concurrency(spans) == 4


def test_idle_slots_show_up_as_parallelism_below_the_slot_count() -> None:
    """The signal that raising BUILD_SLOTS would buy nothing.

    Four slots configured, but only two builds ever overlap: the worker ran out
    of work, not out of capacity.
    """
    spans = intervals_of([(0.0, 10.0), (0.0, 10.0), (10.0, 10.0)])
    assert effective_parallelism(spans) == 30.0 / 20.0
    assert peak_concurrency(spans) == 2


def test_a_gap_between_builds_is_not_counted_as_busy() -> None:
    spans = intervals_of([(0.0, 5.0), (100.0, 5.0)])
    assert busy_seconds(spans) == 10.0
    assert effective_parallelism(spans) == 1.0


def test_a_build_ending_as_another_begins_is_not_double_counted() -> None:
    spans = intervals_of([(0.0, 10.0), (10.0, 10.0)])
    assert peak_concurrency(spans) == 1


def test_zero_length_builds_are_ignored() -> None:
    assert intervals_of([(0.0, 0.0), (1.0, 5.0)]) == ((1.0, 6.0),)
