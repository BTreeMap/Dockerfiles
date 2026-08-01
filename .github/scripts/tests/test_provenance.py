"""Provenance: what an image says it was assembled from.

Two properties carry the design. Resolution is total -- no registry failure may
reach a build -- and labelling is confined to the dependency graph, because a
batch label changes an image's digest on every run and the build pins
SOURCE_DATE_EPOCH monthly precisely so that unchanged images keep theirs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from ci.domain import (
    BatchId,
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
from ci.provenance import (
    BATCH_LABEL,
    CONSUMES_LABEL,
    IMAGE_LABEL,
    _batch_in,
    _built_on,
    _configuration_for,
    label_arguments,
    rendered,
    resolve_all,
    selector_arguments,
)

BATCH = BatchId.derive(run_id="17", run_attempt="1", commit_sha="abc", date_time="t")
OTHER = BatchId.derive(run_id="18", run_attempt="1", commit_sha="abc", date_time="t")


def task(**overrides: Any) -> Task:
    fields: dict[str, Any] = {
        "image": "code-server",
        "dockerfile": "code-server/Dockerfile",
        "context": "code-server",
        "platform": Platform.AMD64,
        "max_retries": 50,
    }
    return Task(**{**fields, **overrides})


def landed(dependency: Dependency, provenance: Provenance) -> ResolvedEdge:
    """An edge as a build records it: what it consumed, from where, and what
    the registry said that was."""
    return ResolvedEdge(dependency, provenance, reference=f"reg:{dependency.image}")


def labels(arguments: tuple[str, ...]) -> dict[str, str]:
    """The `--label k=v` pairs as a mapping, so tests read as assertions."""
    assert all(flag == "--label" for flag in arguments[::2])
    return dict(pair.split("=", 1) for pair in arguments[1::2])


# --- the sum survives serialisation -----------------------------------------


def test_each_variant_renders_its_own_tag() -> None:
    """`state` is explicit rather than inferred from which keys are present.

    A reader that had to notice a missing `batch` to learn a lookup failed would
    be redoing this elimination -- and a hand-written "unknown" in the batch field
    would read as a batch.
    """
    assert rendered(Minted(BATCH, "sha256:aa")) == {
        "state": "minted",
        "batch": str(BATCH),
        "digest": "sha256:aa",
    }
    assert rendered(Unlabelled("sha256:bb")) == {"state": "unlabelled", "digest": "sha256:bb"}
    assert rendered(Unreadable("timed out")) == {"state": "unreadable", "reason": "timed out"}


# --- reading an inspect payload ---------------------------------------------


def configuration(batch: str | None) -> dict[str, Any]:
    return {"config": {"Labels": {} if batch is None else {BATCH_LABEL: batch}}}


def test_the_platform_that_is_building_is_the_one_asked_about() -> None:
    """A dependency's other architecture has no bearing on this build."""
    payload = {
        "linux/amd64": configuration(str(BATCH)),
        "linux/arm64": configuration(str(OTHER)),
    }
    assert _batch_in(_configuration_for(payload, Platform.AMD64) or {}) == BATCH
    assert _batch_in(_configuration_for(payload, Platform.ARM64) or {}) == OTHER


def test_a_single_platform_payload_is_read_directly() -> None:
    """buildx reports one shape for a manifest list and another for an image."""
    assert _configuration_for(configuration(str(BATCH)), Platform.AMD64) is not None


@pytest.mark.parametrize("payload", [None, {}, {"linux/arm64": {}}, "text", []])
def test_a_payload_without_this_platform_yields_absence(payload: Any) -> None:
    assert _configuration_for(payload, Platform.AMD64) is None


@pytest.mark.parametrize(
    "labelled",
    [None, "not-a-batch", "", "A" * 32, str(BATCH).upper()],
)
def test_a_label_that_is_not_a_batch_is_not_believed(labelled: str | None) -> None:
    """A label was written by an earlier run of unknown vintage, so it is parsed.

    Uppercase is the interesting rejection: base32 is case-folded on the way out,
    so a shouted token is not the same identifier and must not pass as one.
    """
    assert _batch_in(configuration(labelled)) is None


def test_a_configuration_without_labels_yields_absence() -> None:
    assert _batch_in({}) is None
    assert _batch_in({"config": {}}) is None


# --- which images are labelled at all ---------------------------------------


def test_an_image_outside_the_graph_carries_no_labels() -> None:
    """Emptiness is the membership answer, so no caller needs a predicate.

    This is also what preserves the monthly digest stability the build's pinned
    SOURCE_DATE_EPOCH exists for: an image with no edges gains nothing that
    changes every run.
    """
    assert label_arguments(task(), BATCH, ()) == ()


def test_a_referenced_image_is_labelled_even_with_no_dependencies_of_its_own() -> None:
    """It must carry a batch, or its consumers have nothing to read.

    `code-server-base` and `code-server-proot` are this case in the real tree:
    no edges out, and unlabelled they would break the mechanism for everything
    downstream of them.
    """
    rendered_labels = labels(
        label_arguments(task(image="code-server-base", dependents=("code-server",)), BATCH, ())
    )

    assert rendered_labels[BATCH_LABEL] == str(BATCH)
    assert rendered_labels[IMAGE_LABEL] == "code-server-base"
    assert CONSUMES_LABEL not in rendered_labels


def test_dependents_decide_membership_but_are_never_published() -> None:
    """Who consumes an image is recoverable from any checkout, so git holds it.

    Every other label describes something only the run knows. This asserts the
    asymmetry directly: the inverted graph changes *whether* labels appear, and
    appears in none of them.
    """
    consumers = ("code-server", "code-server-full")
    rendered_labels = labels(
        label_arguments(task(image="code-server-base", dependents=consumers), BATCH, ())
    )

    assert set(rendered_labels) == {IMAGE_LABEL, BATCH_LABEL}
    assert not any(
        consumer in value for value in rendered_labels.values() for consumer in ("full",)
    )


def test_a_consuming_image_records_the_batch_behind_each_edge() -> None:
    """The diagnostic the whole mechanism is for.

    A base edge resolving to one batch and an artifact edge to another is exactly
    the skew that produces binaries linked against the wrong libraries, and here
    it is legible from the published image alone.
    """
    consuming = task(
        dependencies=(
            Dependency(image="code-server-base", usage=Usage.BASE, argument="REF_BASE"),
            Dependency(image="code-server-go", usage=Usage.ARTIFACT, argument="REF_GO"),
        )
    )
    resolved = (
        landed(consuming.dependencies[0], Minted(BATCH, "sha256:aa")),
        landed(consuming.dependencies[1], Minted(OTHER, "sha256:bb")),
    )

    consumes = json.loads(labels(label_arguments(consuming, BATCH, resolved))[CONSUMES_LABEL])

    assert consumes == [
        {
            "image": "code-server-base",
            "usage": "base",
            "state": "minted",
            "batch": str(BATCH),
            "digest": "sha256:aa",
        },
        {
            "image": "code-server-go",
            "usage": "artifact",
            "state": "minted",
            "batch": str(OTHER),
            "digest": "sha256:bb",
        },
    ]


def test_an_unresolved_edge_is_recorded_rather_than_dropped() -> None:
    """Silence would read as "no dependency"; this reads as "could not tell"."""
    consuming = task(
        dependencies=(
            Dependency(image="code-server-base", usage=Usage.BASE, argument="REF_BASE"),
        )
    )

    consumes = json.loads(
        labels(
            label_arguments(
                consuming,
                BATCH,
                (landed(consuming.dependencies[0], Unreadable("not resolved")),),
            )
        )[CONSUMES_LABEL]
    )

    assert consumes == [
        {
            "image": "code-server-base",
            "usage": "base",
            "state": "unreadable",
            "reason": "not resolved",
        }
    ]


def test_labels_are_byte_stable_for_an_unchanged_graph() -> None:
    """A label is part of the image configuration, so its bytes are its digest.

    Key order and separators are pinned for that reason; a mapping that
    serialised differently on a different run would change the digest of a build
    that had not changed.
    """
    consuming = task(
        dependencies=(
            Dependency(image="a", usage=Usage.BASE, argument="REF_A"),
            Dependency(image="b", usage=Usage.ARTIFACT, argument="REF_B"),
        ),
        dependents=("x", "y"),
    )
    resolved = (
        landed(consuming.dependencies[0], Minted(BATCH, "sha256:aa")),
        landed(consuming.dependencies[1], Unlabelled("sha256:bb")),
    )

    first = label_arguments(consuming, BATCH, resolved)
    assert first == label_arguments(consuming, BATCH, resolved)
    assert " " not in labels(first)[CONSUMES_LABEL]


# --- depth-indexed pinning --------------------------------------------------


def _assignments(arguments: tuple[str, ...]) -> dict[str, str]:
    """The `--build-arg` pairs, unflagged: every second element is a value."""
    return dict(
        (name, value) for pair in arguments[1::2] for name, value in (pair.split("=", 1),)
    )


def edge(image: str, usage: Usage, back: int, provenance: Provenance) -> ResolvedEdge:
    return landed(
        Dependency(image=image, usage=usage, argument=f"REF_{image}", generations_back=back),
        provenance,
    )


def resolving(answers: dict[str, Provenance]) -> Callable[[str, Platform], Provenance]:
    """A stand-in for the registry: a reference either has an answer or is absent.

    Absence is `Unreadable`, which is what a pruned or never-published tag looks
    like from `imagetools inspect`, and is the case the fallback exists for.
    """

    def answer(reference: str, _platform: Platform) -> Provenance:
        return answers.get(reference, Unreadable("inspect exited 1"))

    return answer


def test_a_base_is_pinned_a_generation_behind_the_artifacts_landing_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix, in one assertion.

    An artifact's binaries were compiled against a base one generation older than
    its own batch, so an image cannot sit on the same generation it copies from.
    Level difference is that offset, and the table is indexed by it.
    """
    newest = BatchId.derive(run_id="3", run_attempt="1", commit_sha="a", date_time="t")
    older = BatchId.derive(run_id="2", run_attempt="1", commit_sha="a", date_time="t")
    monkeypatch.setattr(
        "ci.provenance.resolve",
        resolving(
            {
                f"reg:code-server-base.{older}": Minted(older, "sha256:a"),
                f"reg:code-server-go.{newest}": Minted(newest, "sha256:b"),
            }
        ),
    )

    resolved = resolve_all(
        (
            Dependency(
                image="code-server-base", usage=Usage.BASE, argument="REF_BASE", generations_back=2
            ),
            Dependency(
                image="code-server-go", usage=Usage.ARTIFACT, argument="REF_GO", generations_back=1
            ),
        ),
        "reg",
        Platform.AMD64,
        (newest, older),
    )

    # The whole reference, in the registry being published to, not a fragment.
    rendered_args = _assignments(selector_arguments(resolved))
    assert rendered_args["REF_BASE"] == f"reg:code-server-base.{older}"
    assert rendered_args["REF_GO"] == f"reg:code-server-go.{newest}"


def test_what_is_recorded_is_the_image_that_was_actually_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect that made a correct build report itself broken.

    Resolving the floating tag and then building against a pinned one recorded a
    generation the image was not assembled from. Every floating tag in a run
    carries the newest batch, so a base pinned two generations back was published
    as having consumed the newest one -- and the skew check, reading exactly that
    record, reported the disagreement that pinning had just prevented.
    """
    newest = BatchId.derive(run_id="3", run_attempt="1", commit_sha="a", date_time="t")
    older = BatchId.derive(run_id="2", run_attempt="1", commit_sha="a", date_time="t")
    monkeypatch.setattr(
        "ci.provenance.resolve",
        resolving(
            {
                "reg:code-server-base": Minted(newest, "sha256:floating"),
                f"reg:code-server-base.{older}": Minted(older, "sha256:pinned"),
            }
        ),
    )

    (resolved,) = resolve_all(
        (
            Dependency(
                image="code-server-base", usage=Usage.BASE, argument="REF_BASE", generations_back=2
            ),
        ),
        "reg",
        Platform.AMD64,
        (newest, older),
    )

    assert resolved.reference == f"reg:code-server-base.{older}"
    assert resolved.provenance == Minted(older, "sha256:pinned")


def test_an_edge_reaching_past_the_table_floats(monkeypatch: pytest.MonkeyPatch) -> None:
    """A short table is the bootstrap, and floating is what happened before.

    The edges the table does cover are still pinned, so a partially-built table
    is useful rather than all-or-nothing.
    """
    newest = BatchId.derive(run_id="3", run_attempt="1", commit_sha="a", date_time="t")
    monkeypatch.setattr(
        "ci.provenance.resolve",
        resolving({"reg:code-server-base": Unlabelled("sha256:a")}),
    )

    (resolved,) = resolve_all(
        (
            Dependency(
                image="code-server-base", usage=Usage.BASE, argument="REF_BASE", generations_back=2
            ),
        ),
        "reg",
        Platform.AMD64,
        (newest,),
    )

    # Floating, and still a whole reference: the fork's registry, no batch.
    assert _assignments(selector_arguments((resolved,)))["REF_BASE"] == "reg:code-server-base"


def test_a_generation_the_registry_no_longer_holds_falls_back_to_floating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinning describes a build; it must never be the reason one cannot run.

    A table entry names a generation that was complete when it was walked, but a
    tag can be pruned between then and the build. Handing the build a reference
    the registry cannot resolve would turn a lost description into a lost image.
    """
    newest = BatchId.derive(run_id="3", run_attempt="1", commit_sha="a", date_time="t")
    monkeypatch.setattr(
        "ci.provenance.resolve",
        resolving({"reg:code-server-base": Minted(newest, "sha256:a")}),
    )

    (resolved,) = resolve_all(
        (Dependency(image="code-server-base", usage=Usage.BASE, argument="REF_BASE"),),
        "reg",
        Platform.AMD64,
        (newest,),
    )

    assert resolved.reference == "reg:code-server-base"
    assert resolved.provenance == Minted(newest, "sha256:a")


def test_a_dependency_records_what_it_was_itself_built_on() -> None:
    """The level below the batch, and the one a skew is visible at."""
    older = BatchId.derive(run_id="2", run_attempt="1", commit_sha="a", date_time="t")
    payload = {
        "config": {
            "Labels": {
                BATCH_LABEL: str(BATCH),
                CONSUMES_LABEL: json.dumps(
                    [
                        {"image": "code-server-base", "state": "minted", "batch": str(older)},
                        {"image": "code-server-proot", "state": "unreadable", "reason": "x"},
                    ]
                ),
            }
        }
    }
    assert _built_on(payload) == {"code-server-base": older}


def test_an_unparseable_consumes_label_yields_no_claims() -> None:
    """Written by an earlier run of unknown vintage, so nothing is guessed at."""
    for raw in ("not json", "{}", json.dumps([{"image": 1}]), json.dumps(["x"])):
        assert _built_on({"config": {"Labels": {CONSUMES_LABEL: raw}}}) == {}
