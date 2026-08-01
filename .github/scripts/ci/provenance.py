"""What an image was assembled from, recorded on the image itself.

`ci/references.py` recovers the edges of this repository's own dependency graph
from the Dockerfiles. This module answers the question those edges raise -- which
batch was on the other end when the build actually consumed it -- and renders the
answer into labels, so an image published here can be asked what it is made of
without anyone having to reconstruct the run that made it.

Two rules shape the design.

*Provenance is observational, never a precondition.* A registry that will not
answer must not fail a build. Every failure to resolve becomes a recorded
`Unreadable`, which is louder than a missing label and cheaper than a red run.

*Only graph members are labelled.* The batch changes every run, so a batch label
changes an image's digest every run -- and the build pins SOURCE_DATE_EPOCH to
the start of the month precisely so that an unchanged image keeps its digest
across the runs within it. Labelling all thirty images to describe the eight that
have edges would trade that property away for nothing. `label_arguments` returns
nothing at all for an image with no edges in either direction.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, assert_never

from ci.domain import BatchId, Dependency, Platform, Task

logger = logging.getLogger("ci.provenance")

# Reverse-DNS on the repository that publishes these images, so a label of ours
# can never collide with one an upstream base image already carries -- every
# image here is built on somebody else's, and inherits their labels with it.
_PREFIX = "io.github.btreemap.dockerfiles"

IMAGE_LABEL = f"{_PREFIX}.image"
BATCH_LABEL = f"{_PREFIX}.batch"
CONSUMES_LABEL = f"{_PREFIX}.consumes"

# There is deliberately no `consumed-by` label. Who consumes an image is a fact
# about the source tree rather than about the build, recoverable by running the
# parser over any checkout, so recording it on the image would duplicate what git
# already holds. Every other label here describes something only the run knows.
# The inverted graph is still carried on the task -- it decides *membership*, not
# a label of its own. See `label_arguments`.

# Bounded because this runs inside a build worker that is already holding a
# builder and a share of the run's tasks. A registry that has stopped answering
# must cost one edge's worth of waiting, not the job's remaining hours.
_INSPECT_TIMEOUT_SECONDS = 60


# --- what an edge resolved to ----------------------------------------------


@dataclass(frozen=True, slots=True)
class Minted:
    """The dependency carries a batch label: the group it came from is known."""

    batch: BatchId
    digest: str


@dataclass(frozen=True, slots=True)
class Unlabelled:
    """Resolved, but carrying no batch of ours.

    Reachable and expected: an image published before this mechanism existed, or
    one whose edges were added after it was last built. Distinct from `Unreadable`
    because the registry answered -- the digest below is real evidence, and the
    absence of a batch is a fact about that image rather than about the lookup.
    """

    digest: str


@dataclass(frozen=True, slots=True)
class Unreadable:
    """The registry could not be asked, or did not answer in a shape we know."""

    reason: str


Provenance = Minted | Unlabelled | Unreadable


def rendered(provenance: Provenance) -> dict[str, str]:
    """The JSON shape of one resolution, tag included.

    The variant is carried as an explicit `state` rather than being inferred from
    which keys are present. A reader that had to test for the absence of `batch`
    to learn that a lookup failed would be doing the elimination this sum already
    does -- and would read a hand-written `"unknown"` in the batch field as a
    batch. The tag survives serialisation; the sum does not lose its shape on the
    way out.
    """
    match provenance:
        case Minted(batch, digest):
            return {"state": "minted", "batch": str(batch), "digest": digest}
        case Unlabelled(digest):
            return {"state": "unlabelled", "digest": digest}
        case Unreadable(reason):
            return {"state": "unreadable", "reason": reason}
        case unreachable:
            assert_never(unreachable)


# --- asking the registry ----------------------------------------------------


def _configuration_for(payload: Any, platform: Platform) -> Mapping[str, Any] | None:
    """This platform's image configuration inside an imagetools payload.

    Two shapes, because buildx reports one: a multi-platform tag yields a mapping
    keyed by platform string, a single-platform tag yields the configuration
    directly. Asking per platform rather than merging them is deliberate -- the
    build consuming this dependency runs on one architecture, and that
    architecture's answer is the only one that bears on it.
    """
    if not isinstance(payload, Mapping):
        return None
    keyed = payload.get(f"linux/{platform}")
    if isinstance(keyed, Mapping):
        return keyed
    return payload if "config" in payload else None


def _batch_in(configuration: Mapping[str, Any]) -> BatchId | None:
    """The batch this configuration's labels claim, if it carries a valid one.

    A label is a string written by an earlier run of unknown vintage, so it is
    parsed rather than trusted: `BatchId.parse` rejects anything that is not one,
    and the caller records that as `Unlabelled` rather than propagating a value
    no downstream reader could rely on.
    """
    configured = configuration.get("config")
    labels = configured.get("Labels") if isinstance(configured, Mapping) else None
    raw = labels.get(BATCH_LABEL) if isinstance(labels, Mapping) else None
    return BatchId.parse(raw) if isinstance(raw, str) else None


def resolve(reference: str, platform: Platform) -> Provenance:
    """Asks the registry what `reference` currently is, for one platform.

    Total: every failure -- a missing tag, a timeout, a payload in a shape a
    future buildx invented -- lands in `Unreadable` with the reason attached.
    Nothing here may raise, because a build must not fail over a description of
    itself.

    The answer is a snapshot. The build that follows resolves the same floating
    tag again, and a run publishing concurrently could move it in between. That
    race is why the digest is recorded beside the batch: it is what makes a
    disagreement between the label and the image detectable afterwards, and it is
    an argument for eventually pinning these references rather than describing
    them.
    """
    try:
        completed = subprocess.run(
            ("docker", "buildx", "imagetools", "inspect", "--format", "{{json .}}", reference),
            capture_output=True,
            check=False,
            timeout=_INSPECT_TIMEOUT_SECONDS,
        )
        if completed.returncode != 0:
            return Unreadable(f"inspect exited {completed.returncode}")
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return Unreadable(f"{type(error).__name__}: {error}")

    if not isinstance(payload, Mapping):
        return Unreadable("inspect returned a non-object payload")

    manifest = payload.get("manifest")
    digest = manifest.get("digest") if isinstance(manifest, Mapping) else None
    if not isinstance(digest, str):
        return Unreadable("inspect returned no manifest digest")

    configuration = _configuration_for(payload.get("image"), platform)
    if configuration is None:
        return Unreadable(f"no image configuration for linux/{platform}")

    batch = _batch_in(configuration)
    return Unlabelled(digest) if batch is None else Minted(batch, digest)


def resolve_all(
    dependencies: tuple[Dependency, ...], registry_repository: str, platform: Platform
) -> Mapping[str, Provenance]:
    """Resolves every edge of one task, keyed by image name.

    Sequential, and the boundedness argument is the graph's: an image has a
    handful of edges, so the concurrency that matters is already the one between
    tasks. Logged per edge because this is the observable boundary -- a build log
    that says which batch each input came from is the whole point of the exercise.
    """
    resolved = {
        dependency.image: resolve(f"{registry_repository}:{dependency.image}", platform)
        for dependency in dependencies
    }
    for dependency in dependencies:
        logger.info(
            "  consumes %s (%s): %s",
            dependency.image,
            dependency.usage,
            json.dumps(rendered(resolved[dependency.image]), sort_keys=True),
        )
    return resolved


# --- rendering to labels ----------------------------------------------------


def _compact(payload: Any) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace.

    Both properties are load-bearing rather than tidy. A label is part of the
    image configuration, so a mapping that serialised in a different order on a
    different run would change the image's digest without anything having
    changed.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def label_arguments(
    task: Task, batch: BatchId, resolved: Mapping[str, Provenance]
) -> tuple[str, ...]:
    """The `--label` arguments describing this task's place in the graph.

    Empty for an image with no edges in either direction, which is the membership
    rule stated once: emptiness *is* the answer, so no caller needs a predicate
    for whether an image is labelled, and no image outside the graph pays a digest
    change for a batch it has no use for.

    Both directions decide membership, but only one is rendered. An image nothing
    here consumes gains nothing from a batch label; an image something here
    consumes must carry one whether or not it has dependencies of its own, or its
    consumers have nothing to read. `code-server-base` and `code-server-proot` are
    exactly that case -- no edges out, and unlabelled they would silently break
    the mechanism for everything downstream of them.

    `consumes` is omitted when this image has no dependencies, so a root of the
    graph carries no key claiming it depends on nothing.
    """
    if not task.dependencies and not task.dependents:
        return ()

    labels: dict[str, str] = {
        IMAGE_LABEL: task.image,
        BATCH_LABEL: str(batch),
    }
    if task.dependencies:
        labels[CONSUMES_LABEL] = _compact(
            [
                {
                    "image": dependency.image,
                    "usage": str(dependency.usage),
                    **rendered(resolved.get(dependency.image, Unreadable("not resolved"))),
                }
                for dependency in task.dependencies
            ]
        )

    return tuple(
        argument for name, value in labels.items() for argument in ("--label", f"{name}={value}")
    )
