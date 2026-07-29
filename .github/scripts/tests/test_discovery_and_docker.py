"""Dealing is a partition; tagging is a pure function of the run identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from ci.discovery import deal, discover, seed_for
from ci.docker import backoff_seconds, tags_for
from ci.domain import Platform, Task
from ci.env import BuildIdentity

IDENTITY = BuildIdentity(
    date="2026-07-28",
    date_time="2026-07-28.12-00-00",
    commit_sha="abc123",
    base_image="ghcr.io/btreemap/dockerfiles",
)


def tasks(count: int) -> tuple[Task, ...]:
    return tuple(
        Task(
            image=f"image-{index}",
            dockerfile=f"image-{index}/Dockerfile",
            context=f"image-{index}",
            platform=Platform.AMD64,
            max_retries=50,
        )
        for index in range(count)
    )


# --- dealing ---------------------------------------------------------------


@pytest.mark.parametrize("count,workers", [(31, 4), (1, 4), (0, 4), (100, 7), (3, 3)])
def test_deal_is_a_partition(count: int, workers: int) -> None:
    """Disjoint and covering: every task lands in exactly one share.

    This is the invariant the whole design rests on -- one initial owner per
    task means an unreachable peer degrades the run to static partitioning
    rather than dropping work.
    """
    shares = deal(tasks(count), workers, seed=7)
    flattened = [task for share in shares for task in share]

    assert len(shares) == workers
    assert len(flattened) == count
    assert len(set(flattened)) == count
    assert set(flattened) == set(tasks(count))


def test_deal_is_balanced_to_within_one() -> None:
    shares = deal(tasks(31), 4, seed=7)
    sizes = [len(share) for share in shares]
    assert max(sizes) - min(sizes) <= 1


def test_deal_is_reproducible_from_its_seed() -> None:
    assert deal(tasks(20), 4, seed=99) == deal(tasks(20), 4, seed=99)


def test_deal_does_not_mutate_its_input() -> None:
    original = tasks(10)
    snapshot = tuple(original)
    deal(original, 3, seed=1)
    assert original == snapshot


def test_deal_rejects_a_nonsensical_worker_count() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        deal(tasks(5), 0, seed=1)


def test_seed_is_stable_across_processes() -> None:
    """Guards a real bug: hash() on a str is randomised by PYTHONHASHSEED.

    Using it here made the "deterministic" deal differ between the discovery
    process and any attempt to reproduce it.
    """
    assert seed_for(Platform.AMD64) == seed_for(Platform.AMD64)
    assert seed_for(Platform.AMD64) != seed_for(Platform.ARM64)
    assert seed_for(Platform.AMD64) == sum(
        ordinal * (index + 1) for index, ordinal in enumerate(b"amd64")
    )


# --- discovery -------------------------------------------------------------


def test_discover_finds_one_task_per_image_and_platform(tmp_path: Path) -> None:
    for name in ("redis", "nginx"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "Dockerfile").write_text("FROM scratch\n")

    found = discover(tmp_path, (Platform.AMD64, Platform.ARM64), max_retries=50)

    assert len(found) == 4
    assert {task.image for task in found} == {"redis", "nginx"}
    assert {task.platform for task in found} == {Platform.AMD64, Platform.ARM64}
    assert all(task.dockerfile.endswith("Dockerfile") for task in found)
    assert all(not Path(task.dockerfile).is_absolute() for task in found)


def test_discover_returns_nothing_for_an_empty_tree(tmp_path: Path) -> None:
    assert discover(tmp_path, (Platform.AMD64,), max_retries=1) == ()


# --- tagging ---------------------------------------------------------------


def test_tags_cover_every_published_variant() -> None:
    task = tasks(1)[0]
    published = tags_for(task, IDENTITY)

    assert len(published) == len(set(published)) == 7
    assert published[0] == "ghcr.io/btreemap/dockerfiles:image-0.amd64"
    assert all(tag.endswith(".amd64") for tag in published)
    assert all(":image-0." in tag or tag.endswith(":image-0.amd64") for tag in published)


def test_the_run_unique_tag_is_among_the_published_tags() -> None:
    """Reconciliation looks for a tag the build promises to publish.

    If these two drifted apart, reconcile would rebuild every image on every run
    while reporting that nothing had landed.
    """
    from ci.docker import run_tag

    task = tasks(1)[0]
    assert run_tag(task.image, str(task.platform), IDENTITY) in tags_for(task, IDENTITY)


# --- backoff ---------------------------------------------------------------


def test_backoff_is_capped_and_jittered() -> None:
    # Full jitter draws from [0, cap]; the caller sees the upper bound here.
    assert backoff_seconds(1, uniform=lambda _lo, hi: hi) == 1.0
    assert backoff_seconds(4, uniform=lambda _lo, hi: hi) == 8.0
    assert backoff_seconds(50, uniform=lambda _lo, hi: hi) == 60.0
    assert backoff_seconds(3, uniform=lambda lo, _hi: lo) == 0.0
