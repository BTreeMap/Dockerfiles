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

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from itertools import dropwhile
from pathlib import Path
from typing import assert_never

from ci.domain import Dependency, Usage, selector_argument

# The one form a reference to an image of this repository may take. Anchored at
# both ends so nothing may precede the registry argument or follow the selector.
#
# The registry is a build argument rather than a literal path, which is what makes
# the mechanism fork-safe: a fork publishes under its own name, and a hardcoded
# `ghcr.io/upstream/...` would make every internal reference look external to it
# -- no graph, no check, no labels, and a fork silently consuming upstream's
# images while publishing its own. The image name between them stays literal.
_INTERNAL = re.compile(r"^\$\{REGISTRY\}:([A-Za-z0-9._-]+)\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# Everything a reference could name, for the check that catches a reference which
# should have been written in the form above and was not.
_TAG = re.compile(r":([A-Za-z0-9._-]+)(?:\$\{[A-Za-z_][A-Za-z0-9_]*\})?$")


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


# --- what a reference turns out to be ---------------------------------------


@dataclass(frozen=True, slots=True)
class Internal:
    """A reference to an image this repository builds, written so it can be pinned."""

    dependency: Dependency


@dataclass(frozen=True, slots=True)
class Misdeclared:
    """A reference naming one of our images that cannot be pinned to a batch."""

    reference: str
    complaint: str


@dataclass(frozen=True, slots=True)
class External:
    """A reference to an image outside this repository, or to a local stage."""


Classified = Internal | Misdeclared | External

# No fields to vary, so one value serves every occurrence.
_EXTERNAL = External()


def classify(reference: str, usage: Usage, known: frozenset[str]) -> Classified:
    """Decides what one reference is, in a single pass over one regular expression.

    The three outcomes were previously two separate questions -- "is this an edge"
    and "is this a defect" -- asked by two functions over two traversals of the
    same file, each running its own match. Two answers to one question can drift;
    one closed sum cannot, and callers now eliminate it exhaustively instead of
    testing for absence twice.

    Stage aliases need no tracking to be excluded. A `COPY --from=haskell_builder`
    names a stage declared earlier in the same file, and a stage name may hold
    neither `${` nor a colon -- so the canonical pattern rejects it for free, and
    `scratch` and every external base with it.

    Membership is keyed on the image *name*, not on the registry path, which is
    what makes the check fork-safe: a fork that left `ghcr.io/upstream/...` in
    place would otherwise have that reference classified external and never
    checked, and would consume upstream's images while publishing its own.
    """
    canonical = _INTERNAL.match(reference)
    if canonical is not None:
        image, declared = canonical.group(1), canonical.group(2)
        expected = selector_argument(image)
        if declared != expected:
            return Misdeclared(
                reference, f"carries ${{{declared}}}, but {image}'s selector is ${{{expected}}}"
            )
        return Internal(Dependency(image=image, usage=usage))

    tagged = _TAG.search(reference)
    if tagged is None or tagged.group(1) not in known:
        return _EXTERNAL

    image = tagged.group(1)
    return Misdeclared(
        reference,
        f"names {image}, which this repository builds, but is not written as "
        f"${{REGISTRY}}:{image}${{{selector_argument(image)}}}",
    )


class UnpinnableReference(RuntimeError):
    """A Dockerfile names one of our images without a selector it can be pinned by.

    Refused rather than tolerated because tolerating it is invisible. The build
    would pass a selector, the Dockerfile would ignore it, the floating tag would
    resolve to whatever is newest, and the label would still claim the batch that
    was asked for. Every downstream reader would be told something untrue.
    """

    def __init__(self, defects: Mapping[str, tuple[str, ...]]) -> None:
        super().__init__(
            "These references cannot be pinned to a batch:\n"
            + "\n".join(
                f"  {where}:\n" + "\n".join(f"    {detail}" for detail in details)
                for where, details in defects.items()
            )
        )
        self.defects = dict(defects)


def classified_in(text: str, known: frozenset[str]) -> Iterator[Classified]:
    """Every reference one Dockerfile makes, classified, in order of appearance."""
    return (
        classify(reference, usage, known)
        for instruction in logical_lines(text)
        for found in (_reference_in(tuple(instruction.split())),)
        if found is not None
        for reference, usage in (found,)
    )


def _read(text: str, known: frozenset[str]) -> tuple[tuple[Dependency, ...], tuple[str, ...]]:
    """One file's edges and its defects, from one traversal.

    A loop rather than two comprehensions because both outputs come from one
    elimination: `match` over a closed sum with `assert_never` is what makes a
    fourth variant a type error here rather than a silently dropped reference.
    The mutation is confined to two locals that never escape, which is the honest
    backend for a partition Python has no primitive for.

    Edges are deduplicated and ordered by (image, usage) rather than by
    appearance, so a label rendered from them is byte-stable: moving a COPY within
    a file must not change the digest of the image it builds. One image may
    legitimately appear twice with different usages -- built on, then copied out
    of -- so the pair is the unit of identity, not the name.
    """
    edges: list[Dependency] = []
    defects: list[str] = []
    for item in classified_in(text, known):
        match item:
            case Internal(dependency):
                edges.append(dependency)
            case Misdeclared(reference, complaint):
                defects.append(f"{reference}  -- {complaint}")
            case External():
                pass
            case unreachable:
                assert_never(unreachable)
    return tuple(sorted(set(edges), key=Dependency.sort_key)), tuple(defects)


def dependencies_in(text: str, known: frozenset[str] = frozenset()) -> tuple[Dependency, ...]:
    """The edges one Dockerfile declares, deduplicated and ordered.

    `known` defaults to empty because edges do not depend on it -- only the defect
    report does, and a caller asking just for edges has nothing to do with one.
    """
    return _read(text, known)[0]


def _colliding_arguments(definitions: Mapping[str, Path]) -> Mapping[str, tuple[str, ...]]:
    """Images whose names differ only where `selector_argument` folds them.

    `a-b` and `a.b` both become SELECT_A_B, so one would silently pin the other.
    Grouped rather than compared pairwise, which reports each collision once
    instead of once per direction.
    """
    grouped: dict[str, list[str]] = {}
    for image in sorted(definitions):
        grouped.setdefault(selector_argument(image), []).append(image)
    return {
        str(definitions[images[0]]): (
            f"selector argument ${{{argument}}} is claimed by {', '.join(images)}",
        )
        for argument, images in grouped.items()
        if len(images) > 1
    }


def graph(definitions: Mapping[str, Path], root: Path) -> Mapping[str, tuple[Dependency, ...]]:
    """Every image's outgoing edges, for every image the repository defines.

    Raises `UnpinnableReference` if a reference cannot carry a batch, and
    `DanglingReference` if any edge names an image no Dockerfile builds. Both are
    checked here rather than per file because both are questions about the whole
    tree: a name is dangling only if *nothing* defines it, and two image names
    collide as arguments only relative to each other.

    Each Dockerfile is read once and traversed once; `_read` returns both answers
    from that single pass.
    """
    known = frozenset(definitions)
    parsed = {
        image: _read((root / path).read_text(encoding="utf-8"), known)
        for image, path in definitions.items()
    }

    unpinnable = {
        str(definitions[image]): defects for image, (_, defects) in parsed.items() if defects
    }
    collisions = _colliding_arguments(definitions)
    if unpinnable or collisions:
        raise UnpinnableReference({**collisions, **unpinnable})

    edges = {image: found for image, (found, _) in parsed.items()}
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

    Carried on the task to decide whether it is labelled at all, which is the
    one thing this direction is needed for. An image nothing here consumes has
    no use for a batch label; an image something here consumes must carry one
    even when it has no dependencies of its own, or its consumers have nothing
    to read off it.

    Not published as a label. Who consumes an image is a fact about the source
    tree rather than about the build -- recoverable by running this function over
    any checkout -- so recording it on the image would duplicate git.

    Usage is dropped for the same reason: membership does not depend on it, and
    the consumer's own `consumes` label states it more precisely.
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
