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

# A reference split into the path it names and the tag it carries. One
# definition, because the two halves are read by different checks and a
# disagreement between them would be silent: `rsplit(":")` reads `host:5000/img`
# as the path `host` at tag `5000`, while a tag must be the trailing component
# and may not contain a slash. The greedy prefix puts the split at the *last*
# colon, so a registry port stays in the path where it belongs.
_TAGGED = re.compile(r"^(?P<path>.+):(?P<tag>[A-Za-z0-9._-]+)$")


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


# --- one Dockerfile, read once ----------------------------------------------


@dataclass(frozen=True, slots=True)
class _Stage:
    """One FROM: what it is built on, and what this file calls it."""

    reference: str
    alias: str | None


@dataclass(frozen=True, slots=True)
class _Parsed:
    """Everything one Dockerfile says that bears on the graph.

    A record rather than a traversal per question, because the questions are not
    independent: which stage is the base depends on which one is published, and
    which image an argument names depends on a declaration that may appear
    anywhere above it. Parsing twice to answer two of them is how the file's
    facts drift apart from each other.
    """

    stages: tuple[_Stage, ...]
    copied: tuple[str, ...]
    bindings: Mapping[str, str]
    # Arguments this file declares twice with different defaults, already
    # rendered as complaints. The only way free argument naming can go wrong,
    # and refused at the cause rather than detected at the symptom: the build
    # sets an argument once, so a file whose references resolve it two ways would
    # have one of them silently redirected. A bare `ARG NAME` re-import is
    # untouched, which is the form a stage needs to see a global declaration.
    rebound: tuple[str, ...]


def _operand(tokens: Sequence[str]) -> tuple[str, ...]:
    """An instruction's arguments with its flags dropped.

    FROM may carry flags before its argument (`--platform=$BUILDPLATFORM`), so
    the argument is the first token that is not one.
    """
    return tuple(dropwhile(lambda token: token.startswith("--"), tokens))


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


def _stage_in(tokens: Sequence[str]) -> _Stage | None:
    """The stage one FROM declares, or absence if it declares none."""
    operand = _operand(tokens)
    if not operand:
        return None
    alias = operand[2] if len(operand) >= 3 and operand[1].lower() == "as" else None
    return _Stage(reference=operand[0], alias=alias)


def _copied_in(tokens: Sequence[str]) -> str | None:
    """What one COPY copies from, or absence if it copies from the context.

    The reference sits inside a `--from=` flag that may appear among others such
    as `--chown=`, so it is found by name rather than by position.
    """
    return next(
        (
            token.removeprefix("--from=")
            for token in tokens
            if token.lower().startswith("--from=")
        ),
        None,
    )


def _parse(text: str) -> _Parsed:
    """One traversal, from which every question about this file is answered.

    The bindings accumulate as they are read because a declaration binds a name
    that later instructions resolve through; that state is the scope, not an
    optimisation. It is also why a redeclaration is caught here rather than by
    comparing outcomes afterwards -- at this point both defaults are in hand.
    """
    stages: list[_Stage] = []
    copied: list[str] = []
    rebound: list[str] = []
    bindings: dict[str, str] = {}

    for instruction in logical_lines(text):
        tokens = tuple(instruction.split())
        match tokens[0].lower() if tokens else "":
            case "arg":
                for name, default in _bindings_in(tokens[1:]):
                    if bindings.get(name, default) != default:
                        rebound.append(
                            f"${{{name}}}  -- redeclared with a different default: "
                            f"{bindings[name]} then {default}"
                        )
                    bindings[name] = default
            case "from":
                stage = _stage_in(tokens[1:])
                if stage is not None:
                    stages.append(stage)
            case "copy":
                source = _copied_in(tokens)
                if source:
                    copied.append(source)
            case _:
                pass

    return _Parsed(
        stages=tuple(stages),
        copied=tuple(copied),
        bindings=bindings,
        rebound=tuple(rebound),
    )


# --- what a reference turns out to be ---------------------------------------


@dataclass(frozen=True, slots=True)
class Internal:
    """A reference to an image this repository builds, declared so it can be pinned."""

    dependency: Dependency


@dataclass(frozen=True, slots=True)
class Misdeclared:
    """A reference naming one of our images that the build cannot work with."""

    reference: str
    complaint: str


@dataclass(frozen=True, slots=True)
class External:
    """A reference to an image outside this repository, or to a local stage."""


Classified = Internal | Misdeclared | External

# No fields to vary, so one value serves every occurrence.
_EXTERNAL = External()

# BuildKit resolves `COPY --from` before argument expansion and refuses the
# instruction outright rather than copying from something unintended. Refused
# here because the alternative is learning it from a build that has already
# queued, pulled its base, and burned its retry budget.
_NO_EXPANSION = (
    "BuildKit does not expand build arguments inside COPY --from. Hoist it to a "
    "stage instead -- `FROM <the argument> AS <name>` above, then "
    "`COPY --from=<name>`"
)


def _image_named(reference: str, known: frozenset[str]) -> str | None:
    """The image of this repository a reference names, if it names one."""
    found = _TAGGED.match(reference)
    return found["tag"] if found is not None and found["tag"] in known else None


def _path_of(reference: str) -> str | None:
    """The registry path a reference names, without its tag."""
    found = _TAGGED.match(reference)
    return None if found is None else found["path"]


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


def _ancestry(stages: Sequence[_Stage]) -> frozenset[str]:
    """Every stage the published image is built on, following aliases upward.

    The last FROM is what a Dockerfile publishes, so this is what decides whether
    a reference is a base or a source of artifacts. The instruction alone cannot:
    a toolchain file names its base with FROM and publishes `FROM scratch`, while
    a consumer names the images it copies with FROM too, since BuildKit will not
    expand an argument inside `COPY --from`. Only the position of a stage
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


def _classified_in(parsed: _Parsed, known: frozenset[str]) -> Iterator[Classified]:
    """Every reference one Dockerfile makes, classified.

    Only a FROM can yield an edge. A COPY reaches its source through a stage
    alias -- accounted for by that stage's own FROM -- or it is one of the two
    forms the build cannot use, and both are reported rather than parsed into an
    edge that would never have resolved.
    """
    ancestry = _ancestry(parsed.stages)
    aliased = {stage.alias for stage in parsed.stages if stage.alias is not None}

    for stage in parsed.stages:
        usage = Usage.BASE if stage.reference in ancestry else Usage.ARTIFACT
        yield classify(stage.reference, usage, parsed.bindings, known)

    for reference in parsed.copied:
        if reference in aliased:
            continue
        if _ARGUMENT.match(reference):
            yield Misdeclared(reference, _NO_EXPANSION)
            continue
        yield classify(reference, Usage.ARTIFACT, parsed.bindings, known)


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
    """One file's edges and its defects, from one parse.

    A loop rather than two comprehensions because both outputs come from one
    elimination: `match` over a closed sum with `assert_never` is what makes a
    fourth variant a type error here rather than a silently dropped reference.

    Edges are deduplicated and ordered by (image, usage) rather than by
    appearance, so a label rendered from them is byte-stable: moving a stage
    within a file must not change the digest of the image it builds. One image
    may legitimately appear twice with different usages, so the pair is the unit
    of identity, not the name.
    """
    parsed = _parse(text)
    edges: list[Dependency] = []
    defects: list[str] = []
    for item in _classified_in(parsed, known):
        match item:
            case Internal(dependency):
                edges.append(dependency)
            case Misdeclared(reference, complaint):
                defects.append(f"{reference}  -- {complaint}")
            case External():
                pass
            case unreachable:
                assert_never(unreachable)
    return FileFacts(
        edges=tuple(sorted(set(edges), key=Dependency.sort_key)),
        defects=tuple(defects) + parsed.rebound,
        declarations=parsed.bindings,
    )


def dependencies_in(text: str, known: frozenset[str] = frozenset()) -> tuple[Dependency, ...]:
    """The edges one Dockerfile declares, deduplicated and ordered.

    `known` defaults to empty because a reference is internal only if this tree
    builds the image it names, so a caller with no tree in view has no edges.
    """
    return _read(text, known).edges


# --- defects the whole tree defines -----------------------------------------


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


class MisdeclaredReference(RuntimeError):
    """A Dockerfile names one of our images in a form the build cannot use.

    Refused rather than tolerated because tolerating either form is expensive in
    its own way. A literal reference fails silently: the build passes an
    argument, the Dockerfile ignores it, the floating tag resolves to whatever is
    newest, and the label still claims the batch that was asked for, so every
    downstream reader is told something untrue. An argument inside `COPY --from`
    fails loudly, but only after the build has queued, pulled, and worked through
    a retry budget measured in dozens.
    """

    def __init__(self, defects: Mapping[str, tuple[str, ...]]) -> None:
        super().__init__(
            "These references are written in a form the build cannot use:\n"
            + "\n".join(
                f"  {where}:\n" + "\n".join(f"    {detail}" for detail in details)
                for where, details in defects.items()
            )
        )
        self.defects = dict(defects)


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


# --- the graph over a whole tree --------------------------------------------


@dataclass(frozen=True, slots=True)
class Graph:
    """This repository's own dependency graph and the facts derived from it.

    A record rather than four functions over one mapping. Each field is a total
    function of `edges`, so they cannot disagree; kept together because every
    one of them was previously recomputed by a second caller from a second read
    of the tree, which made "the graph" a thing the plan job assembled twice and
    could in principle assemble differently.
    """

    edges: Mapping[str, tuple[Dependency, ...]]
    # Each image's depth: one more than the deepest image it depends on. Level is
    # the whole basis of the pinning rule -- see `Dependency.generations_back`.
    levels: Mapping[str, int]
    # The graph inverted: for each image, who consumes it. Never published as a
    # label, because who consumes an image is a fact about the source tree rather
    # than about the build, recoverable by running this parser over any checkout.
    # It decides whether an image is labelled at all, and nothing else.
    dependents: Mapping[str, tuple[str, ...]]
    # An image with an edge stepping back exactly one generation, and its target:
    # the pair the generation walk steps through. Absent when no chain is deep
    # enough to step through, which is a repository whose images do not build on
    # each other; nothing needs a table then, and the caller floats everything.
    probe: tuple[str, str] | None
    # How far back any edge reaches, which is the generation table's length.
    # Deeper buys nothing: an edge pinned to generation k inherits whatever
    # *that* build was pinned to, so the chain past the table is already baked
    # into the images being named.
    depth: int


def _levels_of(edges: Mapping[str, tuple[Dependency, ...]]) -> Mapping[str, int]:
    """Each image's depth in the graph: one more than the deepest it depends on.

    An image at level L is assembled from generation N-(L-1) of the roots, so the
    difference in level between two images *is* the number of generations between
    the builds that can coherently be combined.

    Recursion is the honest shape here -- a level is defined in terms of the
    levels below it -- and it is safe because the depth it recurses to is the
    graph's, which is four in this repository and bounded by the number of images
    in any repository. Memoised so that a diamond is not re-walked once per path,
    and carrying the path it came by, which is what turns a cycle into a raised
    defect rather than a hang.
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


def _dependents_of(edges: Mapping[str, tuple[Dependency, ...]]) -> Mapping[str, tuple[str, ...]]:
    """The graph inverted. Usage is dropped: membership does not depend on it,
    and the consumer's own `consumes` label states it more precisely."""
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


def _probe_for(edges: Mapping[str, tuple[Dependency, ...]]) -> tuple[str, str] | None:
    """An image with an edge stepping back exactly one generation, and its target.

    Usage does not matter: what the walk reads is `built_on`, which keeps every
    edge it could pin regardless of how the image consumed it. Any such image
    will do, since floating tags advance only as a complete generation and so
    every one of them reports the same batch, which leaves the choice
    alphabetical purely to keep a run reproducible from its inputs.
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


def _dangling_in(
    parsed: Mapping[str, FileFacts], ours: frozenset[str], known: frozenset[str]
) -> Mapping[str, tuple[str, ...]]:
    """Declarations pointing into one of our own paths at an image nothing builds.

    The typo and the deleted Dockerfile, and the reason they are worth refusing:
    such a tag still resolves in the registry, to whatever last published it, so
    the build succeeds and consumes something arbitrarily old, indefinitely.

    This cannot be found among the edges. Internality is decided by the image
    name, so a reference to an image nothing builds is classified external and
    never becomes an edge at all -- which is precisely the case in question. So
    it is found among the declarations instead, against the set of paths the
    resolving declarations revealed this tree to publish under.
    """
    dangling: dict[str, set[str]] = {}
    for image, facts in parsed.items():
        for declared in facts.declarations.values():
            found = _TAGGED.match(declared)
            if found is not None and found["path"] in ours and found["tag"] not in known:
                dangling.setdefault(found["tag"], set()).add(image)
    return {target: tuple(sorted(referrers)) for target, referrers in sorted(dangling.items())}


def graph(definitions: Mapping[str, Path], root: Path) -> Graph:
    """The whole tree's graph, read once.

    Raises `MisdeclaredReference` if a reference is written in a form the build
    cannot use, `DanglingReference` if a declaration names an image no Dockerfile
    builds, and `CyclicGraph` if the images depend on each other in a loop. All
    three are questions about the whole tree rather than about one file: a name
    is dangling only if *nothing* defines it, and whether a literal reference
    should have been a declaration depends on what else the tree builds.
    """
    known = frozenset(definitions)
    parsed = {
        image: _read((root / path).read_text(encoding="utf-8"), known)
        for image, path in definitions.items()
    }

    misdeclared = {
        str(definitions[image]): facts.defects for image, facts in parsed.items() if facts.defects
    }
    if misdeclared:
        raise MisdeclaredReference(misdeclared)

    # The repository paths this tree actually publishes under, inferred from the
    # declarations that resolved rather than configured. Inferred because the
    # parser must stay registry-agnostic to be fork-safe: a fork's checkout still
    # defaults to upstream's path, and hardcoding either one would either miss its
    # typos or reject its references wholesale.
    ours = frozenset(
        path
        for facts in parsed.values()
        for declared in facts.declarations.values()
        if _image_named(declared, known) is not None
        for path in (_path_of(declared),)
        if path is not None
    )
    dangling = _dangling_in(parsed, ours, known)
    if dangling:
        raise DanglingReference(dangling)

    declared_edges = {image: facts.edges for image, facts in parsed.items()}
    levels = _levels_of(declared_edges)

    # Stamped here rather than computed by each consumer: the offset is a fact
    # about the whole graph, and an edge that carried the wrong one would pin to
    # a generation nothing agrees with.
    edges = {
        image: tuple(
            Dependency(
                image=edge.image,
                usage=edge.usage,
                argument=edge.argument,
                generations_back=levels[image] - levels[edge.image],
            )
            for edge in found
        )
        for image, found in declared_edges.items()
    }

    return Graph(
        edges=edges,
        levels=levels,
        dependents=_dependents_of(edges),
        probe=_probe_for(edges),
        depth=max(
            (edge.generations_back for found in edges.values() for edge in found), default=0
        ),
    )
