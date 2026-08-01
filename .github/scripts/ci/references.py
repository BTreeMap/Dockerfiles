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

from ci.domain import Dependency, Usage

# A reference expressed through a build argument, in either spelling Dockerfile
# allows. Anchored at both ends: the whole operand must be the argument, so text
# concatenated onto one is not a declaration this can read.
_ARGUMENT = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$|^\$([A-Za-z_][A-Za-z0-9_]*)$")

# The tag any reference carries, for deciding whether a literal one names an image
# this repository builds and so should have been declared instead.
_TAG = re.compile(r":([A-Za-z0-9._-]+)$")


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
    """A reference to an image this repository builds, declared so it can be pinned."""

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


def _bindings_in(operands: Sequence[str]) -> Iterator[tuple[str, str]]:
    """The (name, default) pairs one ARG instruction establishes.

    `ARG X` with no default binds nothing: it imports an existing argument into a
    stage's scope, which is how a declaration above the first FROM reaches a COPY
    inside one. Reading it as a binding to the empty string would erase the very
    declaration it exists to reach.
    """
    return (
        (name, default)
        for operand in operands
        if "=" in operand
        for name, default in (operand.split("=", 1),)
    )


def _image_named(reference: str, known: frozenset[str]) -> str | None:
    """The image of this repository a reference names, if it names one."""
    found = _TAG.search(reference)
    return found.group(1) if found is not None and found.group(1) in known else None


def classify(
    reference: str, usage: Usage, bindings: Mapping[str, str], known: frozenset[str]
) -> Classified:
    """Decides what one reference is, given the arguments declared above it.

    An internal reference is written as a build argument whose default is the
    reference itself. Without a build system the file resolves exactly those
    images; with one, each argument is replaced by the same image pinned to a
    batch. The argument's *name* carries no meaning: it is read from the same
    declaration the image is read from, so nothing has to connect the two and no
    naming rule can be got wrong.

    Membership is keyed on the image name, which is what keeps this fork-safe. A
    fork's checkout still defaults to upstream's registry, which is the right
    answer for someone building the file by hand, while its CI substitutes its
    own -- and the graph reads the same either way.

    Stage aliases and external bases need no special handling. `haskell_builder`
    is not an argument, and `alpine:3.21` names nothing this tree builds.
    """
    named = _ARGUMENT.match(reference)
    if named is None:
        image = _image_named(reference, known)
        if image is None:
            return _EXTERNAL
        return Misdeclared(
            reference,
            f"names {image}, which this repository builds, but is written literally. "
            f"Declare `ARG <NAME>={reference}` above it and reference ${{<NAME>}}, "
            "so the build can pin it to a batch",
        )

    argument = named.group(1) or named.group(2)
    declared = bindings.get(argument)
    image = None if declared is None else _image_named(declared, known)
    if image is None:
        return _EXTERNAL
    return Internal(Dependency(image=image, usage=usage, argument=argument))


class UnpinnableReference(RuntimeError):
    """A Dockerfile names one of our images in a form the build cannot pin.

    Refused rather than tolerated because tolerating it is invisible. The build
    would pass an argument, the Dockerfile would ignore it, the floating tag would
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


@dataclass(frozen=True, slots=True)
class _Stage:
    """One FROM: what it is built on, and what this file calls it."""

    reference: str
    alias: str | None


def _instructions(
    text: str, collected: dict[str, str]
) -> tuple[tuple[_Stage, ...], tuple[str, ...], tuple[str, ...]]:
    """One pass: the stages this file declares and the sources it copies from.

    The argument bindings accumulate into `collected` as they are read, because a
    declaration binds a name that later instructions resolve through. That state
    is the scope, not an optimisation.
    """
    stages: list[_Stage] = []
    copied: list[str] = []
    rebound: list[str] = []
    for instruction in logical_lines(text):
        tokens = tuple(instruction.split())
        if not tokens:
            continue
        if tokens[0].lower() == "arg":
            for name, default in _bindings_in(tokens[1:]):
                if collected.get(name, default) != default:
                    rebound.append(
                        f"${{{name}}}  -- redeclared with a different default: "
                        f"{collected[name]} then {default}"
                    )
                collected[name] = default
            continue
        found = _reference_in(tokens)
        if found is None:
            continue
        reference, usage = found
        if usage is Usage.ARTIFACT:
            copied.append(reference)
            continue
        operand = tuple(dropwhile(lambda token: token.startswith("--"), tokens[1:]))
        alias = operand[2] if len(operand) >= 3 and operand[1].lower() == "as" else None
        stages.append(_Stage(reference=reference, alias=alias))
    return tuple(stages), tuple(copied), tuple(rebound)


def _ancestry(stages: Sequence[_Stage]) -> frozenset[str]:
    """Every stage the published image is built on, following aliases upward.

    The last FROM is what a Dockerfile publishes, so this is what decides whether
    a reference is a base or a source of artifacts. The instruction alone cannot:
    a toolchain file names its base with FROM and publishes `FROM scratch`, while
    a consumer names the images it copies with FROM too, now that BuildKit will
    not expand an argument inside `COPY --from`. Only the position of a stage
    relative to the final one tells them apart.
    """
    by_alias = {stage.alias: stage for stage in stages if stage.alias is not None}
    ancestry: set[str] = set()
    current: _Stage | None = stages[-1] if stages else None
    while current is not None:
        ancestry.add(current.reference)
        nxt = by_alias.get(current.reference)
        current = nxt if nxt is not None and nxt.reference not in ancestry else None
    return frozenset(ancestry)


def classified_in(
    text: str, known: frozenset[str], collected: dict[str, str] | None = None
) -> Iterator[Classified]:
    """Every reference one Dockerfile makes, classified.

    Two passes over the parsed instructions rather than one over the text: the
    final stage decides how every other reference reads, and it is not known
    until the file has been read to the end.
    """
    bindings = {} if collected is None else collected
    stages, copied, _ = _instructions(text, bindings)
    ancestry = _ancestry(stages)
    aliased = {stage.alias for stage in stages if stage.alias is not None}

    for stage in stages:
        usage = Usage.BASE if stage.reference in ancestry else Usage.ARTIFACT
        yield classify(stage.reference, usage, bindings, known)

    # A COPY naming a stage of this file was already accounted for by that
    # stage's FROM. Anything else is a reference in its own right, and since
    # expansion is unavailable here it can only be a literal one -- which is
    # exactly the misdeclaration the check exists to catch.
    for reference in copied:
        if reference not in aliased:
            yield classify(reference, Usage.ARTIFACT, bindings, known)


def _rebindings_in(text: str) -> tuple[str, ...]:
    """Arguments this file declares twice with different defaults.

    The only way free naming can go wrong, and it is refused at the cause rather
    than detected at the symptom: the build sets an argument once, so a file whose
    references resolve it two ways would have one of them silently redirected. An
    argument declared once and imported later with a bare `ARG NAME` is untouched,
    which is the form a stage needs to see a global declaration.
    """
    return _instructions(text, {})[2]


@dataclass(frozen=True, slots=True)
class FileFacts:
    """Everything one Dockerfile says about this repository's own images.

    Declarations are carried alongside the edges because a dangling reference is
    no longer an edge. Internality is decided by the image name, so a declaration
    naming an image nothing builds classifies as external and vanishes -- which is
    exactly the typo the check has to catch, so the raw declarations have to
    survive the parse for `graph` to compare them against each other.
    """

    edges: tuple[Dependency, ...]
    defects: tuple[str, ...]
    declarations: Mapping[str, str]


def _read(text: str, known: frozenset[str]) -> FileFacts:
    """One file's edges and its defects, from one traversal.

    A loop rather than two comprehensions because both outputs come from one
    elimination: `match` over a closed sum with `assert_never` is what makes a
    fourth variant a type error here rather than a silently dropped reference.

    Edges are deduplicated and ordered by (image, usage) rather than by
    appearance, so a label rendered from them is byte-stable: moving a COPY within
    a file must not change the digest of the image it builds. One image may
    legitimately appear twice with different usages, so the pair is the unit of
    identity, not the name.
    """
    edges: list[Dependency] = []
    defects: list[str] = []
    declarations: dict[str, str] = {}
    for item in classified_in(text, known, declarations):
        match item:
            case Internal(dependency):
                edges.append(dependency)
            case Misdeclared(reference, complaint):
                defects.append(f"{reference}  -- {complaint}")
            case External():
                pass
            case unreachable:
                assert_never(unreachable)
    defects.extend(_rebindings_in(text))
    return FileFacts(
        edges=tuple(sorted(set(edges), key=Dependency.sort_key)),
        defects=tuple(defects),
        declarations=dict(declarations),
    )


def dependencies_in(text: str, known: frozenset[str] = frozenset()) -> tuple[Dependency, ...]:
    """The edges one Dockerfile declares, deduplicated and ordered.

    `known` defaults to empty because a reference is internal only if this tree
    builds the image it names, so a caller with no tree in view has no edges.
    """
    return _read(text, known).edges


def _repository_of(reference: str) -> str:
    """The registry path a reference names, without its tag."""
    return reference.rsplit(":", 1)[0]


def _dangling_in(
    parsed: Mapping[str, FileFacts], ours: frozenset[str] | set[str], known: frozenset[str]
) -> Mapping[str, set[str]]:
    """Declarations pointing into one of our own paths at an image nothing builds.

    The typo and the deleted Dockerfile, and the reason they are worth refusing:
    such a tag still resolves in the registry, to whatever last published it, so
    the build succeeds and consumes something arbitrarily old, indefinitely.

    This can no longer be found among the edges. Internality is decided by the
    image name, so a reference to an image nothing builds is classified external
    and never becomes an edge at all -- which is precisely the case in question.
    So it is found among the declarations instead, against the set of paths the
    resolving declarations revealed this tree to publish under.
    """
    dangling: dict[str, set[str]] = {}
    for image, facts in parsed.items():
        for declared in facts.declarations.values():
            target = declared.rsplit(":", 1)[-1]
            if _repository_of(declared) in ours and target not in known:
                dangling.setdefault(target, set()).add(image)
    return dangling


def graph(definitions: Mapping[str, Path], root: Path) -> Mapping[str, tuple[Dependency, ...]]:
    """Every image's outgoing edges, for every image the repository defines.

    Raises `UnpinnableReference` if a reference cannot carry a batch, and
    `DanglingReference` if any edge names an image no Dockerfile builds. Both are
    checked here rather than per file because both are questions about the whole
    tree: a name is dangling only if *nothing* defines it, and whether a literal
    reference should have been a declaration depends on what else the tree builds.

    Each Dockerfile is read once and traversed once; `_read` returns both answers
    from that single pass.
    """
    known = frozenset(definitions)
    parsed = {
        image: _read((root / path).read_text(encoding="utf-8"), known)
        for image, path in definitions.items()
    }

    unpinnable = {
        str(definitions[image]): facts.defects for image, facts in parsed.items() if facts.defects
    }
    if unpinnable:
        raise UnpinnableReference(unpinnable)

    edges = {image: facts.edges for image, facts in parsed.items()}

    # The repository paths this tree actually publishes under, inferred from the
    # declarations that resolved rather than configured. Inferred because the
    # parser must stay registry-agnostic to be fork-safe: a fork's checkout still
    # defaults to upstream's path, and hardcoding either one would either miss its
    # typos or reject its references wholesale.
    ours = {
        _repository_of(declared)
        for image, facts in parsed.items()
        for name, declared in facts.declarations.items()
        if _image_named(declared, known) is not None
    }
    dangling = {
        target: tuple(sorted(referrers))
        for target, referrers in sorted(_dangling_in(parsed, ours, known).items())
    }
    if dangling:
        raise DanglingReference(dangling)

    # Stamped here rather than computed by each consumer: the offset is a fact
    # about the whole graph, and an edge that carried the wrong one would pin to
    # a generation nothing agrees with.
    level = levels_of(edges)
    return {
        image: tuple(
            Dependency(
                image=edge.image,
                usage=edge.usage,
                argument=edge.argument,
                generations_back=level[image] - level[edge.image],
            )
            for edge in found
        )
        for image, found in edges.items()
    }


class CyclicGraph(RuntimeError):
    """Images of this repository depend on each other in a loop.

    A layout defect, and one that has to be refused rather than worked around:
    a level is defined as one more than the deepest thing below it, which a cycle
    leaves undefined, and Docker could not build such a tree either. Raised for
    the same reason the other two are -- the alternative is a non-terminating
    walk in the plan job.
    """

    def __init__(self, cycle: tuple[str, ...]) -> None:
        super().__init__("These images form a dependency cycle:\n  " + " -> ".join(cycle))
        self.cycle = cycle


def levels_of(edges: Mapping[str, tuple[Dependency, ...]]) -> Mapping[str, int]:
    """Each image's depth in the graph: one more than the deepest it depends on.

    Level is the whole basis of the pinning rule. An image at level L is
    assembled from generation N-(L-1) of the roots, so the difference in level
    between two images *is* the number of generations between the builds that can
    coherently be combined -- see `Dependency.generations_back`.

    Memoised over an explicit stack rather than written as plain recursion:
    Python has no tail-call elimination and this is called once per image, so a
    deep chain in some future repository would be frames rather than iterations.
    The visiting set is what turns a cycle into a raised defect instead of a hang.
    """
    level: dict[str, int] = {}

    def resolve(image: str, visiting: tuple[str, ...]) -> int:
        if image in level:
            return level[image]
        if image in visiting:
            raise CyclicGraph(visiting[visiting.index(image) :] + (image,))
        depth = 1 + max(
            (resolve(edge.image, visiting + (image,)) for edge in edges.get(image, ())),
            default=0,
        )
        level[image] = depth
        return depth

    return {image: resolve(image, ()) for image in edges}


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


def probe_for(edges: Mapping[str, tuple[Dependency, ...]]) -> tuple[str, str] | None:
    """An image with an edge stepping back exactly one generation, and its target.

    The pair the generation walk needs. Usage does not matter: what the walk reads
    is `built_on`, which keeps every edge it could pin regardless of how the image
    consumed it. Any such image will do -- floating tags
    advance only as a complete generation, so every one of them reports the same
    batch -- so the choice is alphabetical purely to keep a run reproducible from
    its inputs.

    Absence when the graph has no chain deep enough to step through, which is a
    repository whose images do not build on each other. Nothing needs a table
    then, and the caller floats everything.
    """
    return next(
        (
            (image, edge.image)
            for image in sorted(edges)
            for edge in edges[image]
            if edge.generations_back == 1
        ),
        None,
    )


def generations_needed(edges: Mapping[str, tuple[Dependency, ...]]) -> int:
    """How far back any edge in this graph reaches.

    The table's length. Deeper than this buys nothing: an edge pinned to
    generation k inherits whatever *that* build was pinned to, so the chain past
    the table is already baked into the images being named.
    """
    return max((edge.generations_back for found in edges.values() for edge in found), default=0)
