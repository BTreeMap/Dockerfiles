"""Domain model for the build mesh.

Every outcome in this module is a closed sum rather than a nullable field or a
boolean flag, so callers eliminate them exhaustively and the type checker can
prove no case was forgotten. The distinctions are not decorative: "the peer had
no spare work" and "the peer was unreachable" lead to the same immediate action
but mean opposite things about the health of a run, and a design that collapses
them makes a broken mesh indistinguishable from a busy one in the logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

# --- refined primitives ----------------------------------------------------


class Platform(StrEnum):
    """The closed set of architectures this repository publishes."""

    AMD64 = "amd64"
    ARM64 = "arm64"

    @classmethod
    def parse(cls, raw: str) -> Self | None:
        try:
            return cls(raw.strip().lower())
        except ValueError:
            return None

    @property
    def runner_label(self) -> str:
        return "ubuntu-24.04" if self is Platform.AMD64 else "ubuntu-24.04-arm"


# A quick-tunnel hostname is interpolated into both a URL and a git ref path, so
# it is refined at one boundary rather than trusted wherever it is used. The
# pattern admits only lowercase alphanumerics, hyphens, and dots under the
# trycloudflare.com suffix -- a set that is simultaneously URL-safe and legal as
# a git ref component, which is what lets the rendezvous carry it in a ref name.
_HOSTNAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9-]+)*\.trycloudflare\.com$")


@dataclass(frozen=True, slots=True)
class Hostname:
    """A validated quick-tunnel hostname."""

    value: str

    def __post_init__(self) -> None:
        # Python cannot hide a dataclass constructor, so the invariant is
        # re-checked here to protect direct construction as well as parse().
        if not _HOSTNAME_PATTERN.match(self.value):
            raise ValueError(f"not a valid quick-tunnel hostname: {self.value!r}")

    @classmethod
    def parse(cls, raw: str) -> Self | None:
        candidate = raw.strip().lower()
        return cls(candidate) if _HOSTNAME_PATTERN.match(candidate) else None

    def __str__(self) -> str:
        return self.value


# --- tasks -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Task:
    """A self-describing unit of build work.

    Carries its own retry budget so it can be handed between machines without
    reference to any external state. That closure property is what makes a steal
    safe: the receiving worker needs nothing from the sender but the task.
    """

    image: str
    dockerfile: str
    context: str
    platform: Platform
    max_retries: int

    @classmethod
    def parse(cls, payload: Any) -> Self | None:
        """Admits an untrusted JSON object into the domain, or rejects it.

        Task lists arrive as environment JSON, which type hints cannot vouch
        for; this is the single boundary where that becomes a trusted value.
        """
        if not isinstance(payload, dict):
            return None

        image = payload.get("image")
        dockerfile = payload.get("dockerfile")
        context = payload.get("context")
        platform = Platform.parse(str(payload.get("platform", "")))
        raw_retries = payload.get("max_retries")

        if not (isinstance(image, str) and image):
            return None
        if not (isinstance(dockerfile, str) and dockerfile):
            return None
        if not isinstance(context, str):
            return None
        if platform is None:
            return None
        if not isinstance(raw_retries, int) or isinstance(raw_retries, bool):
            return None

        return cls(
            image=image,
            dockerfile=dockerfile,
            context=context,
            platform=platform,
            max_retries=raw_retries,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "image": self.image,
            "dockerfile": self.dockerfile,
            "context": self.context,
            "platform": str(self.platform),
            "max_retries": self.max_retries,
        }


# --- build outcomes --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildSucceeded:
    task: Task
    attempts: int
    duration_seconds: float
    # Monotonic clock reading when the build began. Kept alongside the duration
    # so overlaps between concurrent builds can be reconstructed afterwards --
    # which is what turns "are the slots actually busy?" into a measurement
    # rather than an assumption.
    started_at: float = 0.0


@dataclass(frozen=True, slots=True)
class BuildFailed:
    task: Task
    attempts: int
    duration_seconds: float
    error: str
    metrics: dict[str, str]
    started_at: float = 0.0


# Replaces a (success: bool, error: str | None, metrics: dict | None) record in
# which "succeeded but carries an error" and "failed but carries no error" were
# both representable. Neither state exists now.
BuildOutcome = BuildSucceeded | BuildFailed


def succeeded(outcome: BuildOutcome) -> bool:
    return isinstance(outcome, BuildSucceeded)


# --- mesh outcomes ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stolen:
    """The peer handed over work. Non-empty by construction."""

    tasks: tuple[Task, ...]

    def __post_init__(self) -> None:
        if not self.tasks:
            raise ValueError("Stolen must carry at least one task; use PeerEmpty instead")


@dataclass(frozen=True, slots=True)
class PeerEmpty:
    """The peer answered and had nothing spare."""


@dataclass(frozen=True, slots=True)
class PeerUnreachable:
    """The peer could not be asked. Distinct from having nothing to give."""

    reason: str


StealOutcome = Stolen | PeerEmpty | PeerUnreachable


@dataclass(frozen=True, slots=True)
class Drained:
    """The peer confirmed an empty queue."""


@dataclass(frozen=True, slots=True)
class Working:
    pending: int


@dataclass(frozen=True, slots=True)
class HealthUnknown:
    reason: str


# Only Drained is evidence that a peer is finished. HealthUnknown must never be
# read as "done", which is precisely the confusion that would let a worker exit
# while a late-booting peer still holds tasks.
PeerHealth = Drained | Working | HealthUnknown


# --- authentication --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Authenticated:
    body: bytes


@dataclass(frozen=True, slots=True)
class Rejected:
    reason: str


AuthOutcome = Authenticated | Rejected


@dataclass(frozen=True, slots=True)
class HeadersAuthentic:
    """The caller proved knowledge of the key using headers alone.

    This exists so a request can be rejected before its body is read. The body
    is what makes a request expensive to receive, so an unauthenticated caller
    must never get that far -- otherwise the endpoint's capacity limits become
    something an attacker can exhaust without holding the key at all.

    The declared length and digest are carried forward because they are covered
    by the signature: having been authenticated, the length is safe to allocate
    against and the digest is safe to check the body against.
    """

    content_length: int
    body_digest: str


HeaderAuthOutcome = HeadersAuthentic | Rejected


# --- tunnel ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TunnelReady:
    hostname: Hostname


@dataclass(frozen=True, slots=True)
class TunnelUnavailable:
    """The mesh could not be joined. Always survivable: the worker builds alone."""

    reason: str


TunnelStatus = TunnelReady | TunnelUnavailable


# --- egress ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProxyReady:
    """A listening local proxy, addressed two ways because it has two callers.

    `local_url` reaches it from the runner itself. `container_url` reaches it
    from inside a buildkit RUN step, which sits in its own network namespace
    behind the docker bridge and cannot resolve the runner's loopback. Carrying
    both is what stops the wrong one being handed to the wrong consumer -- the
    failure that shape prevents is silent, because a proxy nothing can reach
    looks exactly like a proxy nothing needed.
    """

    local_url: str
    container_url: str


@dataclass(frozen=True, slots=True)
class ProxyUnavailable:
    """No clean egress. Always survivable: builds run on the runner's own path.

    Degrading rather than failing is deliberate. This exists to route around a
    dirty shared network, so it must never become a new reason for a run to go
    red -- on a healthy runner the direct path is what would have been used
    anyway.
    """

    reason: str


EgressStatus = ProxyReady | ProxyUnavailable
