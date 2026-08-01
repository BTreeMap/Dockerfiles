"""Dealing is a partition; tagging is a pure function of the run identity."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ci.discovery import ConflictingDockerfiles, deal, definitions, discover, seed_for
from ci.docker import manifest_tags, tags_for
from ci.domain import BatchId, Platform, Task
from ci.env import BuildIdentity
from ci.retry import backoff_seconds

IDENTITY = BuildIdentity(
    date="2026-07-28",
    date_time="2026-07-28.12-00-00",
    commit_sha="abc123",
    batch=BatchId.derive(
        run_id="17", run_attempt="1", commit_sha="abc123", date_time="2026-07-28.12-00-00"
    ),
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

    found = discover(tmp_path, (Platform.AMD64, Platform.ARM64), max_retries=50).tasks

    assert len(found) == 4
    assert {task.image for task in found} == {"redis", "nginx"}
    assert {task.platform for task in found} == {Platform.AMD64, Platform.ARM64}
    assert all(task.dockerfile.endswith("Dockerfile") for task in found)
    assert all(not Path(task.dockerfile).is_absolute() for task in found)


def test_discover_returns_nothing_for_an_empty_tree(tmp_path: Path) -> None:
    assert discover(tmp_path, (Platform.AMD64,), max_retries=1).tasks == ()


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

    (task,) = discover(tmp_path, (Platform.AMD64,), max_retries=1).tasks

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

    found = discover(tmp_path, (Platform.AMD64,), max_retries=1).tasks

    assert [task.image for task in found] == [
        "alpha",
        "alpha-mike",
        "alpha-zulu",
        "bravo",
    ]
    assert found == discover(tmp_path, (Platform.AMD64,), max_retries=1).tasks


# --- the batch id -----------------------------------------------------------

_BATCH_INPUTS = {
    "run_id": "17",
    "run_attempt": "1",
    "commit_sha": "abc123",
    "date_time": "2026-07-28.12-00-00",
}


def test_a_batch_id_is_a_fixed_width_lowercase_token() -> None:
    """Width and alphabet are what a tag component is allowed to be.

    128 characters is the whole tag's budget, and this token shares it with an
    image name and a platform suffix, so its size may not drift silently.
    """
    batch = BatchId.derive(**_BATCH_INPUTS)

    assert len(batch.value) == 32, batch
    assert set(batch.value) <= set("abcdefghijklmnopqrstuvwxyz234567"), batch
    assert str(batch) == batch.value


@pytest.mark.parametrize("malformed", ["", "abc123", "A" * 32, "x" * 33, "0" * 32, "1" * 32])
def test_a_malformed_batch_id_cannot_be_constructed(malformed: str) -> None:
    """The invariant holds on the constructor, not only on `derive`.

    Python cannot make a dataclass constructor private, so a check that lived
    only in the factory would be a convention. The uppercase and 0/1 cases are
    the ones a hand-written token would plausibly get wrong.
    """
    with pytest.raises(ValueError, match="not a batch id"):
        BatchId(malformed)


def test_the_batch_id_is_derived_not_random() -> None:
    """Every job in a run recomputes it rather than being handed it.

    If this were not a function of its inputs alone, the build, reconcile, and
    manifest stages would each name a different batch and reconciliation would
    rebuild everything on every run.
    """
    assert BatchId.derive(**_BATCH_INPUTS) == BatchId.derive(**_BATCH_INPUTS)


@pytest.mark.parametrize("field", sorted(_BATCH_INPUTS))
def test_every_input_moves_the_batch_id(field: str) -> None:
    """Including the attempt -- a re-run must not land in the batch it replaces.

    Parametrised so that dropping any one input from the material fails with the
    name of the input that stopped mattering.
    """
    changed = {**_BATCH_INPUTS, field: _BATCH_INPUTS[field] + "9"}
    assert BatchId.derive(**changed) != BatchId.derive(**_BATCH_INPUTS)


def test_the_batch_material_cannot_be_reassociated() -> None:
    """Adjacent fields must not be able to trade characters across the boundary.

    A separator-free or space-joined encoding would give run 1/attempt 71 and run
    17/attempt 1 the same material, which is exactly the collision the batch id
    exists to remove.
    """
    assert BatchId.derive(
        run_id="1", run_attempt="71", commit_sha="abc123", date_time="t"
    ) != BatchId.derive(run_id="17", run_attempt="1", commit_sha="abc123", date_time="t")


# --- tagging ---------------------------------------------------------------


def test_tags_cover_every_published_variant() -> None:
    task = tasks(1)[0]
    published = tags_for(task, IDENTITY)

    assert len(published) == len(set(published)) == 6
    assert published[0] == "ghcr.io/btreemap/dockerfiles:image-0.amd64"
    assert all(tag.endswith(".amd64") for tag in published)
    assert all(":image-0." in tag or tag.endswith(":image-0.amd64") for tag in published)


def test_the_superseded_composite_tags_are_no_longer_published() -> None:
    """The batch id is the run-unique pointer; the old attempts at one are gone.

    Asserted rather than left to the tag count so that reinstating either one
    fails here with its own name, instead of as an off-by-one nobody can place.
    """
    task = tasks(1)[0]
    superseded = (
        f"{IDENTITY.commit_sha}.{IDENTITY.date}",
        f"{IDENTITY.commit_sha}.{IDENTITY.date_time}",
    )
    for composite in superseded:
        assert not any(composite in tag for tag in tags_for(task, IDENTITY)), composite
        assert not any(composite in tag for tag in manifest_tags("image-0", IDENTITY)), composite


def test_the_build_and_manifest_tag_sets_agree() -> None:
    """A manifest may not advertise a name no per-platform build published.

    Now true by construction -- one list, suffixed -- so this asserts the
    construction rather than policing two copies of it.
    """
    task = tasks(1)[0]
    stripped = tuple(tag.removesuffix(".amd64") for tag in tags_for(task, IDENTITY))
    assert stripped == manifest_tags("image-0", IDENTITY)


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

    A manifest is fused only from tags carrying this run's batch id, so two runs
    building at the same time cannot contribute images to each other's manifest.
    If a source ever became a floating tag, an overlapping run could swap an
    image out from under a manifest mid-publish.
    """
    from ci.docker import run_tag

    platforms = (Platform.AMD64, Platform.ARM64)
    sources = [run_tag("redis", str(platform), IDENTITY) for platform in platforms]

    for source in sources:
        assert str(IDENTITY.batch) in source, source

    # None of the floating tags the build or manifest stages publish may appear
    # as a manifest source. The date and timestamp are floating now: a re-run of
    # the same commit within one day shares both, so only the batch is evidence.
    task = Task("redis", "redis/Dockerfile", "redis", Platform.AMD64, 50)
    floating = {
        tag
        for tag in (*tags_for(task, IDENTITY), *manifest_tags("redis", IDENTITY))
        if str(IDENTITY.batch) not in tag
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
