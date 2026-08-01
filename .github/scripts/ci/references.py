"""Which images of this repository are built out of which others.

The repository publishes about thirty images to one registry repository, and a
few of them consume each other: `code-server` is built on `code-server-base` and
copies toolchains out of `code-server-go` and its siblings. Those edges are
stated only inside Dockerfiles, where nothing reads them, so the build stage has
never been able to say what it was assembling from what.

This module recovers that graph by reading the Dockerfiles, and it recovers it
by *rule* rather than by list. Nothing here names code-server. Add a second
group of interdependent images tomorrow and its edges appear in the graph, its
members start carrying provenance labels, and a reference to an image nobody
builds fails discovery -- with no edit to this file.

Pure: parsing is a function from text to edges. The registry lookups that turn
an edge into a batch live in `ci/provenance.py`, on the other side of that line.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from itertools import dropwhile
from pathlib import Path

from ci.domain import Dependency, Usage


class DanglingReference(RuntimeError):
    """A Dockerfile consumes an image of this repository that nothing builds.

    A layout defect rather than a runtime case, and a silent one until now. The
    reference still resolves in the registry -- to whatever that tag pointed at
    the last time something published it, which for a deleted or misspelled
    image is a build from an arbitrary point in the past that will keep being
    consumed indefinitely. Raised for the same reason `ConflictingDockerfiles`
    is: the alternative is guessing, and guessing wrong here is invisible.
    """

    def __init__(self, dangling: Mapping[str, tuple[str, ...]]) -> None:
        super().__init__(
            "These Dockerfiles reference images of this repository that no "
            "Dockerfile builds:\n"
            + "\n".join(
                f"  {image}: referenced by {', '.join(referrers)}"
                for image, referrers in dangling.items()
            )
        )
        self.dangling = dict(dangling)


def logical_lines(text: str) -> Iterator[str]:
    """The Dockerfile's instructions, one per element, continuations joined.

    Comment lines are dropped before joining rather than after, which is the
    order the Docker parser uses: a `#` line inside a continuation is a comment,
    while a `#` that follows an argument on the same line is not.
    """
    joined = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        joined += line.removesuffix("\\") if line.endswith("\\") else line
        if not line.endswith("\\"):
            yield joined
            joined = ""
    if joined:
        yield joined


def _reference_in(instruction: Sequence[str]) -> tuple[str, Usage] | None:
    """The image reference this instruction consumes, and how, or absence.

    Only FROM and COPY can name another image. FROM may carry flags before its
    argument (`--platform=$BUILDPLATFORM`), so the argument is the first token
    that is not one; COPY carries its reference inside a `--from=` flag that may
    sit among others such as `--chown=`.
    """
    if not instruction:
        return None
    match instruction[0].lower():
        case "from":
            operand = tuple(dropwhile(lambda token: token.startswith("--"), instruction[1:]))
            return (operand[0], Usage.BASE) if operand else None
        case "copy":
            source = next(
                (
                    token.removeprefix("--from=")
                    for token in instruction
                    if token.lower().startswith("--from=")
                ),
                None,
            )
            return (source, Usage.ARTIFACT) if source else None
        case _:
            return None


def _internal_image(reference: str, registry_repository: str) -> str | None:
    """The image name this reference names within this repository, or absence.

    Stage aliases need no tracking to be excluded, which is why none happens
    here. A `COPY --from=haskell_builder` names a stage declared earlier in the
    same file, and a stage name may hold only alphanumerics, underscores, dots
    and hyphens -- so it can never carry the slashes and colon of a registry
    reference. The prefix test rejects it for free, and `scratch` and every
    external base with it.
    """
    prefix = f"{registry_repository}:".lower()
    if not reference.lower().startswith(prefix):
        return None
    # Everything after the colon is the tag, which for this repository *is* the
    # image name -- the naming scheme in ci/discovery.py puts it there.
    return reference[len(prefix) :] or None


def _edge_in(instruction: str, registry_repository: str) -> Dependency | None:
    """One instruction's edge, or absence -- the composition of the two tests.

    Named rather than inlined so both partial steps keep a place to fail: a line
    that is not FROM or COPY, and a reference that is not this repository's.
    """
    found = _reference_in(tuple(instruction.split()))
    if found is None:
        return None
    reference, usage = found
    image = _internal_image(reference, registry_repository)
    return None if image is None else Dependency(image=image, usage=usage)


def dependencies_in(text: str, registry_repository: str) -> tuple[Dependency, ...]:
    """The edges one Dockerfile declares, deduplicated and ordered.

    Ordered by (image, usage) rather than by appearance so a label rendered from
    this is byte-stable: moving a COPY within a file must not change the digest
    of the image it builds.

    One image may legitimately appear twice with different usages -- built on,
    then copied out of -- so the pair is the unit of identity, not the name.
    """
    edges = filter(
        None, (_edge_in(instruction, registry_repository) for instruction in logical_lines(text))
    )
    return tuple(sorted(set(edges), key=Dependency.sort_key))


def graph(
    definitions: Mapping[str, Path], registry_repository: str, root: Path
) -> Mapping[str, tuple[Dependency, ...]]:
    """Every image's outgoing edges, for every image the repository defines.

    Raises `DanglingReference` if any edge names an image no Dockerfile builds.
    Checked here rather than per file because the question is about the whole
    tree: a reference is dangling only if *nothing* defines its target.
    """
    edges = {
        image: dependencies_in((root / path).read_text(encoding="utf-8"), registry_repository)
        for image, path in definitions.items()
    }
    dangling = {
        target: tuple(
            sorted(image for image, found in edges.items() if any(d.image == target for d in found))
        )
        for target in sorted({d.image for found in edges.values() for d in found})
        if target not in definitions
    }
    if dangling:
        raise DanglingReference(dangling)
    return edges


def dependents_of(edges: Mapping[str, tuple[Dependency, ...]]) -> Mapping[str, tuple[str, ...]]:
    """The graph inverted: for each image, who consumes it.

    Carried on the task so a published image can say what depends on it, not
    only what it depends on. That direction is the one a reader needs when
    deciding whether an image is safe to change, and it is unavailable from the
    image itself -- nothing downstream has been built yet when it is published.

    Usage is deliberately dropped: an image's dependents are a fact about the
    repository's shape, and recording *how* each consumes it here would duplicate
    what that consumer's own `consumes` label already states more precisely.
    """
    return {
        image: tuple(
            sorted(
                consumer
                for consumer, found in edges.items()
                if any(dependency.image == image for dependency in found)
            )
        )
        for image in edges
    }


def images_in_graph(edges: Mapping[str, tuple[Dependency, ...]]) -> frozenset[str]:
    """The images that participate in the repository's own graph, either way.

    The membership rule the provenance labels key on. A referenced image must
    carry a batch label for its consumers to be able to read one, and a
    referencing image must carry one to be comparable against what it consumed,
    so both directions are members. Everything else stays unlabelled and keeps a
    digest that changes only when its content does.
    """
    referenced = {dependency.image for found in edges.values() for dependency in found}
    return frozenset(referenced | {image for image, found in edges.items() if found})
