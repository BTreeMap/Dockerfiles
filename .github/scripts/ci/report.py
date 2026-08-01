"""Job-summary markdown: what a human needs to see when something looks wrong.

Pure. Every function here is a fold from records to lines, so what a run will
report can be asserted in a test without a registry or a runner.

Three rules shape the layout, and they are in tension:

*Answer the first question first.* Reading a summary starts with "is anything
wrong", not "tell me about image 23". Each section opens with one line that
settles that, and only then offers the detail behind it.

*Detail costs attention, so charge for it.* A job summary competes with the log
below it. Rows that carry no information -- an isolated image with no edges, a
build that behaved -- are folded into a count or a `<details>`, which keeps the
scannable part short without losing anything.

*Never render a state the reader has to decode.* A batch id is 32 characters of
base32 and nine of them side by side is a wall. They are abbreviated for reading
and shown in full exactly where a reader would copy one.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import assert_never

from ci.domain import (
    BatchId,
    BuildOutcome,
    Dependency,
    Minted,
    Provenance,
    ResolvedEdge,
    Unlabelled,
    Unreadable,
    Usage,
    succeeded,
)
from ci.env import BuildIdentity
from ci.references import Graph

# A digest is 71 characters and only ever wanted as evidence, not as an
# identifier to compare by eye, so enough of it to be unambiguous is enough. A
# batch id is not truncated at all: it is the value a reader copies into a tag or
# greps the log for, and a table with four columns has the room.
_DIGEST_SHOWN = 23


def _state_of(provenance: Provenance) -> tuple[str, str]:
    """One edge's state as (marker, detail), eliminating every variant.

    The marker is what a reader scans for; the detail is what they act on. Kept
    together because a marker without its reason sends the reader to the log,
    which is the cost this report exists to avoid.
    """
    match provenance:
        case Minted(batch, _):
            return "pinned", f"`{batch}`"
        case Unlabelled(digest):
            return "floating", f"no batch label on `{digest[:_DIGEST_SHOWN]}`"
        case Unreadable(reason):
            return "**floating**", reason
        case unreachable:
            assert_never(unreachable)


def _edge_rows(image: str, edges: Sequence[ResolvedEdge]) -> Iterator[str]:
    """One row per edge: what was consumed, how, from how far back, and what it was.

    The offset is shown because without it every row of one image looks alike
    while carrying different generations, which is exactly the reading that made
    a correctly pinned build look wrong.
    """
    return (
        f"| `{image}` | `{edge.dependency.image}` | {edge.dependency.usage} "
        f"| N-{edge.dependency.generations_back} | {marker} | {detail} |"
        for edge in edges
        for marker, detail in (_state_of(edge.provenance),)
    )


def _claims_of(edge: ResolvedEdge) -> Mapping[str, BatchId]:
    """Which generation of which ancestor this one edge commits the image to.

    A base edge commits the image to *being* that batch of that image, and to
    everything that build in turn sat on. An artifact edge commits nothing about
    what the image runs on -- its binaries merely have to have been compiled
    against the same ancestors, which is what its own record says.
    """
    found = edge.provenance
    if not isinstance(found, Minted):
        return {}
    if edge.dependency.usage is Usage.BASE:
        return {edge.dependency.image: found.batch, **found.built_on}
    return found.built_on


def _skew_in(edges: Sequence[ResolvedEdge]) -> str | None:
    """Whether one image's edges disagree about an ancestor they share.

    The single fact this whole report exists to surface, and it is a question
    one level below the batch. A batch says which generation an edge belongs to;
    `built_on` says which generation its *contents* were compiled against, and an
    ancestor claimed at two batches is exactly the case where binaries meet
    libraries they were not linked against.

    Comparing the edges' own batches would answer a different and useless
    question. Depth-indexed pinning gives them different batches on purpose, and
    before that pinning existed they all carried the same one, so neither reading
    ever bore on whether the pieces fit.

    Edges that could not be resolved claim nothing and so cannot disagree, which
    is why a first run against an unlabelled registry reports no skew rather than
    nothing but skew.
    """
    claimed: dict[str, set[str]] = {}
    for edge in edges:
        for ancestor, batch in _claims_of(edge).items():
            claimed.setdefault(ancestor, set()).add(str(batch))

    disputed = sorted(ancestor for ancestor, batches in claimed.items() if len(batches) > 1)
    if not disputed:
        return None
    return "assembled from two generations of " + ", ".join(f"`{name}`" for name in disputed)


def provenance_section(heading: str, outcomes: Iterable[BuildOutcome]) -> tuple[str, ...]:
    """What each built image was assembled from, and whether the pieces agree.

    Returns nothing at all when no build in this job had a dependency, which is
    the common case for a worker holding only isolated images -- an empty section
    reading "no edges" would cost a reader a glance to learn nothing.
    """
    with_edges = tuple(outcome for outcome in outcomes if outcome.edges)
    if not with_edges:
        return ()

    skewed = {
        outcome.task.image: detail
        for outcome in with_edges
        for detail in (_skew_in(outcome.edges),)
        if detail is not None
    }
    unresolved = sum(
        1
        for outcome in with_edges
        for edge in outcome.edges
        if not isinstance(edge.provenance, Minted)
    )

    verdict = (
        f"**SKEW: {len(skewed)} image(s) assembled from more than one generation**"
        if skewed
        else "**OK**: no image is assembled from more than one generation of an ancestor"
    )

    return (
        "",
        f"### {heading}",
        "",
        f"- {verdict}",
        f"- {sum(len(outcome.edges) for outcome in with_edges)} edge(s) across "
        f"{len(with_edges)} image(s), {unresolved} unpinned",
        *(f"  - `{image}`: {detail}" for image, detail in sorted(skewed.items())),
        "",
        "| Image | Consumes | As | Reaches | Pin | Batch or reason |",
        "| --- | --- | --- | --- | --- | --- |",
        *(
            row
            for outcome in sorted(with_edges, key=lambda o: o.task.image)
            for row in _edge_rows(outcome.task.image, outcome.edges)
        ),
    )


def run_section(
    identity: BuildIdentity,
    generations: Sequence[BatchId],
    needed: int,
    probe: str | None,
    images: int,
    platforms: int,
) -> tuple[str, ...]:
    """What this run *is*, for someone opening the summary to troubleshoot.

    The batch leads because every question about a published image starts with
    it: it names the tag to look for, it is what the labels record, and it is what
    a reader greps the log for. Shown in full for that reason.

    The generation table follows because it is the run's most consequential
    input and the least visible. A short table is not an error and not obviously
    wrong from anywhere else, yet it silently means edges reaching past its end
    were left floating, which is the difference between the mechanism working and
    quietly doing nothing.
    """
    short = len(generations) < needed
    lines = [
        "",
        "### This run",
        "",
        f"- batch **`{identity.batch}`**; every image published now is tagged "
        f"`<image>.{identity.batch}`",
        f"- commit `{identity.commit_sha}`, planned at `{identity.date_time}` UTC",
        f"- publishing {images} image(s) to `{identity.base_image}` for {platforms} platform(s)",
    ]
    if needed == 0:
        lines.append("- no image depends on another here, so no generations are needed")
        return tuple(lines)

    lines.append(
        f"- generation table: **{len(generations)} of {needed}** resolved"
        + (f", walked through `{probe}`" if probe else "")
        + ("; edges reaching past the end are left floating" if short else "")
    )
    lines.extend(
        f"  {position}. `{batch}` (N-{position})"
        for position, batch in enumerate(generations, start=1)
    )
    if not generations:
        lines.append(
            "  - none resolved, so every reference falls back to its floating tag. "
            "Expected on a registry with no provenance labels yet; otherwise check "
            "the plan job's log for why the probe did not resolve"
        )
    return tuple(lines)


def _unreached(edges: Sequence[Dependency], resolved: int) -> str:
    """Marks an image whose edges could not all be pinned this run.

    The figure beside it is where the design puts this image once the generation
    table is full. An edge reaching further back than the table goes falls back to
    its floating tag, so until then the image is assembled from something else,
    and a column that stayed silent about it would be stating the intent while
    looking like a measurement.
    """
    deepest = max((edge.generations_back for edge in edges), default=0)
    return "" if deepest <= resolved else f" (not reached: needs {deepest} generations)"


def _oldest_content(level: int) -> str:
    """How old the deepest thing inside an image at this level is.

    `level - 1`, and not the largest offset on the image's own edges, which is
    the number a per-edge table cannot show. `code-server-full` pins
    `code-server` one generation back, but that build had already pinned its own
    base two further back, so its oldest layers are three generations old while
    every edge on its row reads one or two.
    """
    return "this run" if level <= 1 else f"N-{level - 1}"


def graph_section(found: Graph, resolved: int) -> tuple[str, ...]:
    """The repository's own dependency graph, once, from the plan job.

    Structure rather than outcome: it changes when a Dockerfile changes, not when
    a run happens. Reported separately for that reason -- a reader comparing two
    runs should see this section identical, which makes any difference in it
    worth reading.

    The images with no edges are a count and a fold-out. They are most of the
    repository and they carry no provenance labels, so listing them inline would
    push the part that matters off the first screen.

    `resolved` is how many generations this run actually has, and it is here so
    the staleness column cannot state a design property as though it were a fact.
    An edge reaching further back than the table goes floats, so an image with one
    is not composed the way this table would otherwise claim, and saying so is the
    difference between describing the run and describing the intent.
    """
    edges, dependents, levels = found.edges, found.dependents, found.levels
    members = sorted(image for image in edges if edges[image] or dependents[image])
    isolated = sorted(set(edges) - set(members))
    deepest = max((levels[image] for image in members), default=1)
    oldest = sorted(image for image in members if levels[image] == deepest)

    return (
        "",
        "### Image dependency graph",
        "",
        f"- **{len(members)}** image(s) reference each other; "
        f"these carry provenance labels and pinned references",
        f"- **{len(isolated)}** isolated image(s) build from outside this repository only",
        "- a **generation** is one completed run, not a fixed span of time: pushes to "
        "`main` produce generations as well as the schedule, so how old a generation is "
        "depends on how often the repository changes",
        f"- the oldest content in any image here is **{_oldest_content(deepest)}**, in "
        + _joined(f"`{image}`" for image in oldest)
        + ". That is the transitive figure and it is not the largest number on an "
        "image's own row: pinning an edge *k* generations back means the build it "
        "names had already pinned its own edges further back still",
        "- an edge reaching back *k* is pinned to the *k*-th newest generation, because "
        "an artifact's binaries were compiled against a base one generation older than "
        "its own batch, so the base it lands on has to be that much older too or they "
        "disagree",
        "",
        "| Image | Oldest content | Consumes (usage, generations back) | Consumed by |",
        "| --- | --- | --- | --- |",
        *(
            f"| `{image}` | {_oldest_content(levels[image])}{_unreached(edges[image], resolved)} "
            f"| {_joined(_edge_label(d) for d in edges[image])} "
            f"| {_joined(f'`{name}`' for name in dependents[image])} |"
            for image in members
        ),
        "",
        "<details><summary>Isolated images</summary>",
        "",
        _joined(f"`{image}`" for image in isolated),
        "",
        "</details>",
    )


def _edge_label(dependency: Dependency) -> str:
    """One edge as the graph table shows it: what, how, and how far back."""
    return f"`{dependency.image}` ({dependency.usage}, {dependency.generations_back} back)"


def _joined(parts: Iterable[str]) -> str:
    """Spells emptiness out, so a blank cell never reads as a missing value.

    Matches `MatrixEntry.summary`, which already had to answer this question.
    """
    return ", ".join(parts) or "(none)"


def outcome_rows(outcomes: Sequence[BuildOutcome], dealt: frozenset[str]) -> tuple[str, ...]:
    """Per-image build results: failures first, then slowest first.

    Both keys earn their place. Failures lead because a reader scanning thirty
    rows for the one that broke should not have to scan. Duration orders the rest
    because the slowest image is the floor no amount of parallelism can go below,
    which is the reason these timings are recorded at all -- an alphabetical table
    would have quietly retired that.
    """
    return tuple(
        f"| `{outcome.task.image}.{outcome.task.platform}` "
        f"| {'ok' if succeeded(outcome) else '**failed**'} "
        f"| {outcome.attempts} "
        f"| {outcome.duration_seconds / 60:.1f} min "
        f"| {'dealt' if outcome.task.image in dealt else 'stolen'} |"
        for outcome in sorted(outcomes, key=lambda o: (succeeded(o), -o.duration_seconds))
    )
