"""Task discovery and dealing: the single source of truth for a run's work list.

Both the build and manifest stages consume this output instead of globbing
independently, so the two can no longer disagree about which images exist.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from pathlib import Path

from ci.domain import Platform, Task


def discover(root: Path, platforms: Iterable[Platform], max_retries: int) -> tuple[Task, ...]:
    """Builds one task per (image, platform), ordered deterministically.

    Sorting first makes the deal reproducible from the seed alone: the shuffle
    is only well-defined if what it shuffles is.
    """
    dockerfiles = sorted(root.glob("**/Dockerfile"))
    return tuple(
        Task(
            image=dockerfile.parent.name.lower(),
            dockerfile=str(dockerfile.relative_to(root)),
            context=str(dockerfile.parent.relative_to(root)),
            platform=platform,
            max_retries=max_retries,
        )
        for dockerfile in dockerfiles
        for platform in platforms
    )


def deal(tasks: Sequence[Task], worker_count: int, seed: int) -> tuple[tuple[Task, ...], ...]:
    """Splits tasks into `worker_count` disjoint shares.

    Dealing is random rather than cost-ordered because no free cost proxy exists
    here: build contexts are a few kilobytes each and every image fetches its
    real weight from the network, so context size carries no signal about
    duration. Work stealing is what corrects the resulting imbalance, and it
    needs no cost estimates at all.

    `sample` rather than `shuffle` so the input sequence is not mutated, and
    stride slicing rather than an accumulator loop because a stride *is* a
    round-robin deal -- which makes the partition visibly total and
    cardinality-preserving: every index lands in exactly one share.
    """
    if worker_count < 1:
        raise ValueError("worker_count must be at least 1")

    shuffled = random.Random(seed).sample(tuple(tasks), len(tasks))
    return tuple(tuple(shuffled[offset::worker_count]) for offset in range(worker_count))


def seed_for(platform: Platform) -> int:
    """A stable per-platform seed.

    Deliberately not `hash()`, which is randomised per process by PYTHONHASHSEED
    and would make a run impossible to reproduce from its inputs. Independent
    seeds per platform mean a slow image lands beside differently-loaded
    neighbours on each side.
    """
    return sum(ordinal * (index + 1) for index, ordinal in enumerate(platform.encode()))
