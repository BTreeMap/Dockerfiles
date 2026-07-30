"""Dealing is a partition; tagging is a pure function of the run identity."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ci.discovery import ConflictingDockerfiles, deal, definitions, discover, seed_for
from ci.docker import tags_for
from ci.domain import Platform, Task
from ci.env import BuildIdentity
from ci.retry import backoff_seconds

IDENTITY = BuildIdentity(
    date="2026-07-28",
    date_time="2026-07-28.12-00-00",
    commit_sha="abc123",
    base_image="ghcr.io/btreemap/dockerfiles",
)


def tasks(count: int) -> tuple[Task, ...]:
    return tuple(
        Task(
            image=f"image-{index}",
            dockerfile=f"image-{index}/Dockerfile",
            context=f"image-{index}",
            platform=Platform.AMD64,
            max_retries=50,
        )
        for index in range(count)
    )


# --- dealing ---------------------------------------------------------------


@pytest.mark.parametrize("count,workers", [(31, 4), (1, 4), (0, 4), (100, 7), (3, 3)])
def test_deal_is_a_partition(count: int, workers: int) -> None:
    """Disjoint and covering: every task lands in exactly one share.

    This is the invariant the whole design rests on -- one initial owner per
    task means an unreachable peer degrades the run to static partitioning
    rather than dropping work.
    """
    shares = deal(tasks(count), workers, seed=7)
    flattened = [task for share in shares for task in share]

    assert len(shares) == workers
    assert len(flattened) == count
    assert len(set(flattened)) == count
    assert set(flattened) == set(tasks(count))


def test_deal_is_balanced_to_within_one() -> None:
    shares = deal(tasks(31), 4, seed=7)
    sizes = [len(share) for share in shares]
    assert max(sizes) - min(sizes) <= 1


def test_deal_is_reproducible_from_its_seed() -> None:
    assert deal(tasks(20), 4, seed=99) == deal(tasks(20), 4, seed=99)


def test_deal_does_not_mutate_its_input() -> None:
    original = tasks(10)
    snapshot = tuple(original)
    deal(original, 3, seed=1)
    assert original == snapshot


def test_deal_rejects_a_nonsensical_worker_count() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        deal(tasks(5), 0, seed=1)


def test_seed_is_stable_across_processes() -> None:
    """Guards a real bug: hash() on a str is randomised by PYTHONHASHSEED.

    Using it here made the "deterministic" deal differ between the discovery
    process and any attempt to reproduce it.

    Checked by computing the seed in fresh interpreters under deliberately
    hostile hash seeds, rather than by restating the formula. A test that
    duplicates the implementation asserts only that the code is the code -- it
    would have passed with hash() in place, which is the defect this exists to
    catch.
    """
    scripts = str(Path(__file__).resolve().parents[1])
    program = (
        f"import sys; sys.path.insert(0, {scripts!r});"
        "from ci.discovery import seed_for;"
        "from ci.domain import Platform;"
        "print(seed_for(Platform.AMD64))"
    )

    observed = {
        subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": setting},
        ).stdout.strip()
        for setting in ("0", "1", "4294967295", "random")
    }

    assert observed == {str(seed_for(Platform.AMD64))}
    assert seed_for(Platform.AMD64) != seed_for(Platform.ARM64)


# --- discovery -------------------------------------------------------------


def test_discover_finds_one_task_per_image_and_platform(tmp_path: Path) -> None:
    for name in ("redis", "nginx"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "Dockerfile").write_text("FROM scratch\n")

    found = discover(tmp_path, (Platform.AMD64, Platform.ARM64), max_retries=50)

    assert len(found) == 4
    assert {task.image for task in found} == {"redis", "nginx"}
    assert {task.platform for task in found} == {Platform.AMD64, Platform.ARM64}
    assert all(task.dockerfile.endswith("Dockerfile") for task in found)
    assert all(not Path(task.dockerfile).is_absolute() for task in found)


def test_discover_returns_nothing_for_an_empty_tree(tmp_path: Path) -> None:
    assert discover(tmp_path, (Platform.AMD64,), max_retries=1) == ()


def test_a_prefixed_dockerfile_names_the_image_its_sibling_directory_would(
    tmp_path: Path,
) -> None:
    """`<dir>/<stem>.Dockerfile` and `<dir>-<stem>/Dockerfile` are one image.

    The whole point of the second layout: a variant can move next to the thing it
    varies without the published tag moving with it.
    """
    (tmp_path / "code-server").mkdir()
    (tmp_path / "code-server" / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "code-server" / "base.Dockerfile").write_text("FROM scratch\n")

    nested = definitions(tmp_path)

    (tmp_path / "code-server" / "base.Dockerfile").unlink()
    (tmp_path / "code-server-base").mkdir()
    (tmp_path / "code-server-base" / "Dockerfile").write_text("FROM scratch\n")

    assert set(nested) == set(definitions(tmp_path)) == {"code-server", "code-server-base"}


def test_a_prefixed_dockerfile_builds_from_its_own_directory(tmp_path: Path) -> None:
    """The context is the file's directory, not a directory named after the image.

    `code-server-base` built from `code-server/base.Dockerfile` therefore sees
    `code-server/`, which is what makes the two layouts interchangeable only up
    to the image name -- never up to the context.
    """
    (tmp_path / "code-server").mkdir()
    (tmp_path / "code-server" / "base.Dockerfile").write_text("FROM scratch\n")

    (task,) = discover(tmp_path, (Platform.AMD64,), max_retries=1)

    assert task.image == "code-server-base"
    assert task.dockerfile == str(Path("code-server") / "base.Dockerfile")
    assert task.context == "code-server"


def test_two_spellings_of_one_image_are_refused_rather_than_resolved(tmp_path: Path) -> None:
    """Both layouts naming one image is a layout defect, not a tie to break.

    Either candidate is an equally good guess, and picking one would publish the
    wrong image silently -- so discovery refuses and names both claimants.
    """
    (tmp_path / "code-server").mkdir()
    (tmp_path / "code-server" / "base.Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "code-server-base").mkdir()
    (tmp_path / "code-server-base" / "Dockerfile").write_text("FROM scratch\n")

    with pytest.raises(ConflictingDockerfiles) as raised:
        discover(tmp_path, (Platform.AMD64,), max_retries=1)

    assert set(raised.value.conflicts) == {"code-server-base"}
    assert set(raised.value.conflicts["code-server-base"]) == {
        tmp_path / "code-server" / "base.Dockerfile",
        tmp_path / "code-server-base" / "Dockerfile",
    }
    assert "code-server/base.Dockerfile" in str(raised.value)
    assert "code-server-base/Dockerfile" in str(raised.value)


def test_discovery_is_ordered_and_reproducible_across_both_layouts(tmp_path: Path) -> None:
    """`deal` is only reproducible from its seed if what it shuffles is ordered.

    Two globs mean two streams, so the union is sorted rather than each stream
    -- otherwise every `*.Dockerfile` would sort after every plain one and the
    order would depend on which pattern found what.
    """
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "alpha" / "zulu.Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "bravo").mkdir()
    (tmp_path / "bravo" / "Dockerfile").write_text("FROM scratch\n")
    (tmp_path / "alpha" / "mike.Dockerfile").write_text("FROM scratch\n")

    found = discover(tmp_path, (Platform.AMD64,), max_retries=1)

    assert [task.image for task in found] == [
        "alpha",
        "alpha-mike",
        "alpha-zulu",
        "bravo",
    ]
    assert found == discover(tmp_path, (Platform.AMD64,), max_retries=1)


# --- tagging ---------------------------------------------------------------


def test_tags_cover_every_published_variant() -> None:
    task = tasks(1)[0]
    published = tags_for(task, IDENTITY)

    assert len(published) == len(set(published)) == 7
    assert published[0] == "ghcr.io/btreemap/dockerfiles:image-0.amd64"
    assert all(tag.endswith(".amd64") for tag in published)
    assert all(":image-0." in tag or tag.endswith(":image-0.amd64") for tag in published)


def test_the_run_unique_tag_is_among_the_published_tags() -> None:
    """Reconciliation looks for a tag the build promises to publish.

    If these two drifted apart, reconcile would rebuild every image on every run
    while reporting that nothing had landed.
    """
    from ci.docker import run_tag

    task = tasks(1)[0]
    assert run_tag(task.image, str(task.platform), IDENTITY) in tags_for(task, IDENTITY)


# --- backoff ---------------------------------------------------------------


def test_backoff_is_capped_and_jittered() -> None:
    # Full jitter draws from [0, cap]; the caller sees the upper bound here.
    assert backoff_seconds(1, uniform=lambda _lo, hi: hi) == 1.0
    assert backoff_seconds(4, uniform=lambda _lo, hi: hi) == 8.0
    assert backoff_seconds(50, uniform=lambda _lo, hi: hi) == 60.0
    assert backoff_seconds(3, uniform=lambda lo, _hi: lo) == 0.0


# --- manifest sources must never be floating -------------------------------


def test_manifest_sources_are_run_unique_never_floating() -> None:
    """The property that makes concurrent runs safe to overlap.

    A manifest is fused only from tags carrying both the commit and the run's
    timestamp, so two runs building at the same time cannot contribute images to
    each other's manifest. If a source ever became a floating tag, an overlapping
    run could swap an image out from under a manifest mid-publish.
    """
    from ci.docker import run_tag
    from create_docker_manifests import manifest_tags

    platforms = (Platform.AMD64, Platform.ARM64)
    sources = [run_tag("redis", str(platform), IDENTITY) for platform in platforms]

    for source in sources:
        assert IDENTITY.commit_sha in source, source
        assert IDENTITY.date_time in source, source

    # None of the floating tags the build or manifest stages publish may appear
    # as a manifest source.
    task = Task("redis", "redis/Dockerfile", "redis", Platform.AMD64, 50)
    floating = {
        tag
        for tag in (*tags_for(task, IDENTITY), *manifest_tags("redis", IDENTITY))
        if IDENTITY.date_time not in tag
    }
    assert floating, "expected some floating tags to exist"
    assert not (set(sources) & floating)


# --- the build matrix -------------------------------------------------------


def test_matrix_entry_serialises_the_row_the_workflow_consumes() -> None:
    """The runner label is derived, never carried alongside the platform.

    This used to be a `dict[str, object]` assembled inline, so every field came
    back out as `object` and the runner could in principle disagree with the
    architecture it was meant to build. Deriving it from the platform makes that
    pair unable to drift.
    """
    from ci.discovery import MatrixEntry

    task = Task("redis", "redis/Dockerfile", "redis", Platform.ARM64, 50)
    entry = MatrixEntry(platform=Platform.ARM64, worker_id=2, tasks=(task,))
    encoded = entry.as_json()

    assert encoded["platform"] == "arm64"
    assert encoded["worker_id"] == 2
    assert encoded["runner"] == Platform.ARM64.runner_label
    # Tasks are serialised through Task's own schema, so a worker parses back
    # exactly what discovery dealt.
    assert Task.parse(encoded["tasks"][0]) == task


def test_matrix_entry_summarises_an_empty_share_readably() -> None:
    """A worker dealt nothing must be visible as such in the log, not blank."""
    from ci.discovery import MatrixEntry

    assert MatrixEntry(platform=Platform.AMD64, worker_id=0, tasks=()).summary == "(none)"
