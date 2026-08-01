"""The job summary: a pure fold from records to markdown.

The tests that matter are the ones about *attention*. A summary competes with
the log below it, so what earns a row, what folds away, and what leads are the
decisions worth pinning -- a report nobody reads is worse than none.
"""

from __future__ import annotations

from ci.domain import (
    BatchId,
    BuildFailed,
    BuildSucceeded,
    Dependency,
    Minted,
    Platform,
    Provenance,
    ResolvedEdge,
    Task,
    Unlabelled,
    Unreadable,
    Usage,
)
from ci.env import BuildIdentity
from ci.report import (
    graph_section,
    outcome_rows,
    provenance_section,
    run_section,
)
from tests.test_provenance import BATCH, OTHER


def task(image: str, *dependencies: Dependency) -> Task:
    return Task(
        image=image,
        dockerfile=f"{image}/Dockerfile",
        context=image,
        platform=Platform.AMD64,
        max_retries=1,
        dependencies=dependencies,
    )


def built(
    image: str, *edges: tuple[Dependency, Provenance], seconds: float = 60.0
) -> BuildSucceeded:
    return BuildSucceeded(
        task=task(image, *(dependency for dependency, _ in edges)),
        attempts=1,
        duration_seconds=seconds,
        edges=tuple(ResolvedEdge(dependency, provenance) for dependency, provenance in edges),
    )


BASE = Dependency(image="code-server-base", usage=Usage.BASE)
GO = Dependency(image="code-server-go", usage=Usage.ARTIFACT)


# --- the fact the report exists to surface ----------------------------------


def test_an_ancestor_claimed_at_two_generations_is_called_out() -> None:
    """The real skew, which comparing the edges' own batches cannot see.

    Both edges here carry the *same* batch -- as every edge of a run does, since
    floating tags advance only as a complete generation. The disagreement is one
    level down: the base *is* generation BATCH, while the artifact's binaries were
    compiled against generation OTHER of that same base. The first version of this
    check compared edge batches and so reported agreement on exactly this case.
    """
    lines = provenance_section(
        "W",
        [
            built(
                "code-server",
                (BASE, Minted(BATCH, "sha256:a")),
                (GO, Minted(BATCH, "sha256:b", {"code-server-base": OTHER})),
            )
        ],
    )

    assert any("**SKEW:" in line and "1 image(s)" in line for line in lines)
    assert any("two generations of `code-server-base`" in line for line in lines)


def test_an_ancestor_claimed_consistently_reports_agreement() -> None:
    lines = provenance_section(
        "W",
        [
            built(
                "code-server",
                (BASE, Minted(BATCH, "sha256:a")),
                (GO, Minted(BATCH, "sha256:b", {"code-server-base": BATCH})),
            )
        ],
    )
    assert any("**OK**" in line for line in lines)
    assert not any("SKEW" in line for line in lines)


def test_a_floating_edge_is_not_counted_as_disagreement() -> None:
    """An edge with no batch has nothing to disagree with.

    Counting it would raise a warning on every first run, when nothing is
    labelled yet -- and a warning that fires when things are fine is one nobody
    reads when they are not.
    """
    lines = provenance_section(
        "W", [built("code-server", (BASE, Minted(BATCH, "sha256:a")), (GO, Unlabelled("sha256:b")))]
    )
    assert any("**OK**" in line for line in lines)
    assert any("1 unpinned" in line for line in lines)


def test_an_unreadable_edge_reports_its_reason_in_the_row() -> None:
    """A marker without its reason sends the reader to the log."""
    lines = provenance_section("W", [built("x", (BASE, Unreadable("inspect exited 1")))])
    assert any("inspect exited 1" in line for line in lines)


# --- what earns space -------------------------------------------------------


def test_a_job_with_no_edges_reports_nothing_at_all() -> None:
    """Most workers hold only isolated images; an empty section costs a glance."""
    assert provenance_section("W", [built("redis")]) == ()


def test_isolated_images_are_folded_away_but_not_lost() -> None:
    """They are most of the repository and carry no provenance.

    Inline they would push the eight that matter off the first screen; dropped
    entirely, a reader could not confirm an image was discovered at all.
    """
    edges = {"base": (), "app": (Dependency(image="base", usage=Usage.BASE),), "alone": ()}
    dependents = {"base": ("app",), "app": (), "alone": ()}
    levels = {"base": 1, "app": 2, "alone": 1}

    lines = graph_section(edges, dependents, levels, resolved=9)
    rendered = "\n".join(lines)

    assert "<details><summary>Isolated images</summary>" in rendered
    assert "`alone`" in rendered
    assert "**2** image(s) reference each other" in rendered
    assert "**1** isolated image(s)" in rendered
    # The members are in the scannable table, not behind the fold.
    assert rendered.index("| `app` ") < rendered.index("<details>")


def test_an_empty_cell_reads_as_empty_rather_than_missing() -> None:
    edges = {"base": (), "app": (Dependency(image="base", usage=Usage.BASE),)}
    dependents = {"base": ("app",), "app": ()}
    levels = {"base": 1, "app": 2}
    assert any(
        line.endswith("| (none) |") for line in graph_section(edges, dependents, levels, resolved=9)
    )


# --- ordering ---------------------------------------------------------------


def test_failures_lead_and_the_slowest_survivor_follows() -> None:
    """Two keys, both load-bearing.

    Failures first so nobody scans for them; duration next because the slowest
    image is the floor parallelism cannot beat, which is why these timings are
    recorded at all.
    """
    outcomes = (
        built("quick", seconds=10.0),
        built("slow", seconds=600.0),
        BuildFailed(
            task=task("broken"),
            attempts=3,
            duration_seconds=1.0,
            error="boom",
            metrics={},
        ),
    )

    images = [row.split("`")[1] for row in outcome_rows(outcomes, frozenset())]
    assert images == ["broken.amd64", "slow.amd64", "quick.amd64"]


# --- what a run says about itself -------------------------------------------


def identity(batch: BatchId) -> BuildIdentity:
    return BuildIdentity(
        date="2026-08-01",
        date_time="2026-08-01.04-00-00",
        commit_sha="170bd6e",
        batch=batch,
        base_image="ghcr.io/example/dockerfiles",
    )


def test_the_batch_leads_and_is_shown_whole() -> None:
    """Every question about a published image starts from the batch.

    It names the tag to look for, it is what the labels record, and it is what a
    reader greps a log for, so it is not abbreviated.
    """
    lines = run_section(identity(BATCH), (BATCH,), 1, "probe", 38, 2)
    assert any(str(BATCH) in line and "batch" in line for line in lines)
    assert any("<image>." + str(BATCH) in line for line in lines)


def test_a_short_table_says_so_and_says_what_it_costs() -> None:
    """The run's most consequential input and its least visible one.

    A short table is not an error and is invisible from anywhere else, yet it
    means edges past its end were left floating, which is the difference between
    the mechanism working and quietly doing nothing.
    """
    lines = run_section(identity(BATCH), (BATCH,), 2, "probe", 38, 2)
    assert any("**1 of 2**" in line for line in lines)
    assert any("floating" in line for line in lines)


def test_an_empty_table_explains_both_readings() -> None:
    """Expected while bootstrapping, a defect afterwards; the reader is told both."""
    lines = run_section(identity(BATCH), (), 2, "probe", 38, 2)
    assert any("no provenance labels yet" in line for line in lines)
    assert any("check the plan job's log" in line for line in lines)


def test_a_graph_needing_no_generations_says_that_instead() -> None:
    """A repository whose images do not build on each other needs no table."""
    lines = run_section(identity(BATCH), (), 0, None, 5, 1)
    assert any("no generations are needed" in line for line in lines)
    assert not any("floating" in line for line in lines)


def test_the_table_answers_how_stale_an_image_is_in_total() -> None:
    """The question a per-edge table cannot answer.

    A chain of four levels reads "1 back" on its top row while its oldest layers
    are three generations old, because the build it names had already pinned its
    own edges further back. Level minus one is that transitive figure, and it is
    what a reader actually wants to know.
    """
    edges = {
        "base": (),
        "mid": (Dependency(image="base", usage=Usage.BASE, generations_back=1),),
        "top": (Dependency(image="mid", usage=Usage.BASE, generations_back=1),),
        "apex": (Dependency(image="top", usage=Usage.BASE, generations_back=1),),
    }
    dependents = {"base": ("mid",), "mid": ("top",), "top": ("apex",), "apex": ()}
    levels = {"base": 1, "mid": 2, "top": 3, "apex": 4}

    rendered = "\n".join(graph_section(edges, dependents, levels, resolved=9))

    assert "| `base` | this run |" in rendered
    assert "| `mid` | N-1 |" in rendered
    assert "| `apex` | N-3 |" in rendered
    # Stated above the table too, naming the image that carries it.
    assert "oldest content in any image here is **N-3**" in rendered
    assert "`apex`" in rendered
    # And the distinction spelled out, since every edge on that row reads "1 back".
    assert "not the largest number on an image's own row" in rendered


def test_an_image_the_table_could_not_reach_is_marked_as_such() -> None:
    """The staleness figure is where the design puts an image, not where it is.

    With a short generation table an edge reaching past its end falls back to its
    floating tag, so the image is assembled from something other than the column
    claims. Silence there would state the intent while looking like a measurement,
    which is the reading the first version of this table invited.
    """
    edges = {
        "base": (),
        "mid": (Dependency(image="base", usage=Usage.BASE, generations_back=1),),
        "top": (Dependency(image="mid", usage=Usage.BASE, generations_back=2),),
    }
    dependents = {"base": ("mid",), "mid": ("top",), "top": ()}
    levels = {"base": 1, "mid": 2, "top": 3}

    reached = "\n".join(graph_section(edges, dependents, levels, resolved=2))
    short = "\n".join(graph_section(edges, dependents, levels, resolved=1))

    assert "not reached" not in reached
    assert "| `top` | N-2 (not reached: needs 2 generations) |" in short
    # The image whose edges all fit is not marked, even in the same short run.
    assert "| `mid` | N-1 |" in short
