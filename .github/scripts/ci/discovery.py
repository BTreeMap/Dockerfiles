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
from typing import Any

from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass as pydantic_dataclass

from ci.derive import Derivation, Scope
from ci.domain import Platform, Task
from ci.references import dependents_of, graph

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


def discover(
    root: Path, platforms: Iterable[Platform], max_retries: int, registry_repository: str
) -> tuple[Task, ...]:
    """Builds one task per (image, platform), ordered deterministically.

    Each task carries both directions of its place in the repository's own
    dependency graph, resolved here because this is where the whole tree is in
    view: whether an image is consumed by another is not a fact any single
    Dockerfile can state.

    Raises `ConflictingDockerfiles` if the tree defines one image twice, and
    `DanglingReference` if one consumes an image nothing here builds.
    """
    found = definitions(root)
    edges = graph(found, registry_repository, root)
    dependents = dependents_of(edges)
    return tuple(
        Task(
            image=image,
            dockerfile=str(dockerfile.relative_to(root)),
            context=str(dockerfile.parent.relative_to(root)),
            platform=platform,
            max_retries=max_retries,
            dependencies=edges[image],
            dependents=dependents[image],
        )
        for image, dockerfile in found.items()
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


@pydantic_dataclass(frozen=True)
class MatrixEntry:
    """One row of the build matrix: a worker, its platform, and its share.

    A typed record rather than the `dict[str, object]` this used to be. That bag
    mixed a string, an int, and a list of dicts under one key type, so every
    field came back out as `object` and had to be re-narrowed by hand at each
    use -- `", ".join(task["image"] for task in entry["tasks"])` was iterating a
    value the checker could only prove was *something*.

    Serialisation lives in `as_json`, matching `Task.as_json`, so the workflow's
    wire format has one definition and the runner label cannot drift from the
    platform it belongs to.
    """

    platform: Platform
    worker_id: int
    tasks: tuple[Task, ...]

    @property
    def summary(self) -> str:
        """The images in this share, for one log line."""
        return ", ".join(task.image for task in self.tasks) or "(none)"

    def as_json(self) -> dict[str, Any]:
        """The matrix row, serialised by the same schema that declares it.

        `runner` is grafted on rather than stored, because it is a function of
        the platform: keeping it as a field would make a row that names one
        architecture and a runner for another representable, and that pairing is
        the one mistake here nothing downstream could detect.
        """
        encoded: dict[str, Any] = _MATRIX_ENTRY.dump_python(self, mode="json")
        return encoded | {"runner": self.platform.runner_label}


_MATRIX_ENTRY: TypeAdapter[MatrixEntry] = TypeAdapter(MatrixEntry)


# Eight bytes because the consumer is `random.Random`, which takes an arbitrary
# integer; more entropy than that buys a deal no better distributed. The scope
# is what keeps a seed from ever coinciding with a mesh tag or a batch id, all
# three being the same primitive.
_SEED = Derivation(scope=Scope(b"deal-seed-v1"), width=8)


def seed_for(platform: Platform) -> int:
    """A stable per-platform seed.

    Deliberately not `hash()`, which is randomised per process by PYTHONHASHSEED
    and would make a run impossible to reproduce from its inputs. Independent
    seeds per platform mean a slow image lands beside differently-loaded
    neighbours on each side.

    BLAKE2b rather than a hand-rolled weighted sum: the previous construction
    was correct but had to be read carefully to be believed, and its diffusion
    was an accident of the arithmetic rather than a property anyone had
    established. This one is the same primitive the mesh already signs with, so
    the repository has one hash function and one reason for it.
    """
    return _SEED.of(str(platform)).integer()
