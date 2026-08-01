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
    Task,
    Unlabelled,
    Unreadable,
    Usage,
    succeeded,
)

# Enough to tell two batches apart at a glance and to search the log for, while
# staying narrow enough that a row of them still reads as a row. The full value
# is one `docker inspect` away and is never the thing being compared by eye.
_ABBREVIATED = 8


def abbreviate(batch: object) -> str:
    return f"`{str(batch)[:_ABBREVIATED]}…`"


def _state_of(provenance: Provenance) -> tuple[str, str]:
    """One edge's state as (marker, detail), eliminating every variant.

    The marker is what a reader scans for; the detail is what they act on. Kept
    together because a marker without its reason sends the reader to the log,
    which is the cost this report exists to avoid.
    """
    match provenance:
        case Minted(batch, _):
            return "pinned", abbreviate(batch)
        case Unlabelled(digest):
            return "floating", f"no batch label ({digest[:14]}…)"
        case Unreadable(reason):
            return "**floating**", reason
        case unreachable:
            assert_never(unreachable)


def _edge_rows(image: str, edges: Sequence[ResolvedEdge]) -> Iterator[str]:
    return (
        f"| `{image}` | `{edge.dependency.image}` | {edge.dependency.usage} | {marker} | {detail} |"
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

    The single fact this whole report exists to surface, and the one a naive
    check misses. Comparing the edges' *own* batches proves nothing: floating
    tags advance only as a complete generation, so every edge of a run reports
    the same batch and they always agree while the image is still assembled from
    two. What matters is one level down -- which generation each edge's contents
    were compiled against -- and that is what `built_on` records.

    An ancestor claimed at two batches is exactly the case where binaries meet
    libraries they were not linked against. Edges that could not be resolved
    claim nothing and so cannot disagree, which is also why a first run against
    an unlabelled registry reports no skew rather than nothing but skew.
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
        f"⚠️ **{len(skewed)} image(s) assembled from more than one batch**"
        if skewed
        else "✅ every image's edges agree on one batch"
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
        "| Image | Consumes | As | Pin | Batch or reason |",
        "| --- | --- | --- | --- | --- |",
        *(
            row
            for outcome in sorted(with_edges, key=lambda o: o.task.image)
            for row in _edge_rows(outcome.task.image, outcome.edges)
        ),
    )


def graph_section(
    edges: Mapping[str, tuple[Dependency, ...]], dependents: Mapping[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """The repository's own dependency graph, once, from the plan job.

    Structure rather than outcome: it changes when a Dockerfile changes, not when
    a run happens. Reported separately for that reason -- a reader comparing two
    runs should see this section identical, which makes any difference in it
    worth reading.

    The images with no edges are a count and a fold-out. They are most of the
    repository and they carry no provenance labels, so listing them inline would
    push the part that matters off the first screen.
    """
    members = sorted(image for image in edges if edges[image] or dependents[image])
    isolated = sorted(set(edges) - set(members))
    reach = {image: max((d.generations_back for d in edges[image]), default=0) for image in edges}
    deepest = max(reach.values(), default=0)

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
        f"- the deepest edge reaches **{deepest} generation(s)** back. An edge reaching "
        "back *k* is pinned to the *k*-th newest generation, because an artifact's "
        "binaries were compiled against a base one generation older than its own batch -- "
        "so the base it lands on has to be that much older too, or they disagree",
        "",
        "| Image | Consumes (usage, generations back) | Consumed by |",
        "| --- | --- | --- |",
        *(
            f"| `{image}` "
            f"| {_joined(f'`{d.image}` ({d.usage}, −{d.generations_back})' for d in edges[image])} "
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


def _joined(parts: Iterable[str]) -> str:
    """An em dash for emptiness, so a blank cell never reads as a missing value."""
    return ", ".join(parts) or "—"


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


def task_edge_rows(tasks: Iterable[Task]) -> tuple[str, ...]:
    """The edges a set of tasks declares, before anything has been resolved.

    Used by the plan job, which knows the graph but has resolved nothing: it
    reports what *will* be pinned, so a Dockerfile change can be reviewed against
    it without waiting for a build.
    """
    return tuple(
        f"| `{task.image}` | `{dependency.image}` | {dependency.usage} |"
        for task in sorted({task.image: task for task in tasks}.values(), key=lambda t: t.image)
        for dependency in task.dependencies
    )
