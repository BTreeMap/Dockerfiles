"""Task discovery and dealing: the single source of truth for a run's work list.

Both the build and manifest stages consume this output instead of globbing
independently, so the two can no longer disagree about which images exist.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from itertools import groupby
from operator import itemgetter
from pathlib import Path

from ci.domain import Platform, Task

# Each glob is paired with the naming rule it implies, so no layout can be
# admitted without stating what it is called. The two rules agree by
# construction: `<dir>/<stem>.Dockerfile` and `<dir>-<stem>/Dockerfile` name the
# same image. That is deliberate -- it lets a directory keep its variants beside
# the thing they vary (`code-server/base.Dockerfile`) instead of scattering them
# across sibling top-level directories, without moving the published tag.
_LAYOUTS: tuple[tuple[str, Callable[[Path], str]], ...] = (
    ("**/Dockerfile", lambda path: path.parent.name),
    (
        "**/*.Dockerfile",
        lambda path: f"{path.parent.name}-{path.name.removesuffix('.Dockerfile')}",
    ),
)


class ConflictingDockerfiles(RuntimeError):
    """Two or more paths claim one image name.

    A repository-layout defect rather than a runtime case, so it is raised
    rather than folded into the result. The naming rules above are many-to-one
    on purpose, which makes exactly one spelling of an image permissible at a
    time; when both exist, either candidate is an equally good guess and
    publishing the wrong one is silent.
    """

    def __init__(self, conflicts: Mapping[str, tuple[Path, ...]]) -> None:
        super().__init__(
            "Each image must be defined by exactly one Dockerfile, but these "
            "names are claimed more than once:\n"
            + "\n".join(
                f"  {image}: " + ", ".join(str(path) for path in paths)
                for image, paths in conflicts.items()
            )
        )
        self.conflicts = dict(conflicts)


def _claims(root: Path) -> Iterator[tuple[str, Path]]:
    """Every (image name, Dockerfile) the tree asserts, before uniqueness holds."""
    return (
        (name_of(dockerfile).lower(), dockerfile)
        for pattern, name_of in _LAYOUTS
        for dockerfile in root.glob(pattern)
    )


def definitions(root: Path) -> Mapping[str, Path]:
    """The tree's image definitions: one path per name, ordered by name.

    This is the boundary where an untrusted directory tree becomes a trusted
    work list. Downstream code may index by image name because uniqueness is
    established here once, not re-checked at every use.

    Sorting is not cosmetic. It is what makes `deal` reproducible from its seed,
    and it is also what lets `groupby` see each name's claimants together -- one
    ordering serving both.
    """
    grouped = {
        image: tuple(path for _, path in claims)
        for image, claims in groupby(sorted(_claims(root)), key=itemgetter(0))
    }
    conflicts = {image: paths for image, paths in grouped.items() if len(paths) > 1}
    if conflicts:
        raise ConflictingDockerfiles(conflicts)
    return {image: paths[0] for image, paths in grouped.items()}


def discover(root: Path, platforms: Iterable[Platform], max_retries: int) -> tuple[Task, ...]:
    """Builds one task per (image, platform), ordered deterministically.

    Raises `ConflictingDockerfiles` if the tree defines one image twice.
    """
    return tuple(
        Task(
            image=image,
            dockerfile=str(dockerfile.relative_to(root)),
            context=str(dockerfile.parent.relative_to(root)),
            platform=platform,
            max_retries=max_retries,
        )
        for image, dockerfile in definitions(root).items()
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
