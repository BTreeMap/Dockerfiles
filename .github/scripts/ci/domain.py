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
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import BeforeValidator, Field, TypeAdapter, ValidationError
from pydantic.dataclasses import dataclass as pydantic_dataclass

from ci.derive import Derivation, Scope

# --- refined primitives ----------------------------------------------------


# 20 bytes is 32 base32 characters exactly, since 160 bits divides by five and
# so encodes without padding. Width is the point rather than entropy: the run id
# and attempt already identify an execution exactly, and a digest over them can
# only lose information. What it buys is one fixed-size token, comparable at a
# glance across the images sharing it, instead of a composite that grows with
# whatever GitHub's run counter reaches.
_BATCH = Derivation(scope=Scope(b"batch-id-v1"), width=20)

_BATCH_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz234567")


@dataclass(frozen=True, slots=True)
class BatchId:
    """Names the group of images one execution of the workflow publishes.

    Nominal rather than a bare `str`, for the reason `Hostname` below is: a
    `BuildIdentity` carries four strings, and while they were all `str` nothing
    but argument order stopped a commit from being passed where a batch was
    wanted. Here the type checker stops it.

    The invariant is checked in `__post_init__` rather than only in `derive`,
    because Python cannot make the constructor private and a batch id that is
    not 32 base32 characters would be a tag component of unpredictable width.
    """

    value: str

    def __post_init__(self) -> None:
        if len(self.value) != 32 or not _BATCH_ALPHABET.issuperset(self.value):
            raise ValueError(f"not a batch id: {self.value!r}")

    @classmethod
    def derive(cls, run_id: str, run_attempt: str, commit_sha: str, date_time: str) -> Self:
        """Mints the batch for one execution.

        Pure and total. Every stage of a run derives the token rather than
        being handed it, from four values the plan job pinned, so the build,
        reconcile, and manifest stages cannot come to disagree about which
        batch they are in.

        The attempt is mixed in because GITHUB_RUN_ID is stable across re-runs
        and only the attempt increments, so without it a fresh run of the plan
        would publish into the batch it was replacing. It must be the attempt
        the *plan* observed, not the live one -- see `env.BuildIdentity`, where
        that distinction is the difference between a partial re-run reusing the
        images already in the registry and rebuilding all of them.
        """
        return cls(_BATCH.of(run_id, run_attempt, commit_sha, date_time).base32())

    def __str__(self) -> str:
        """The tag algebra interpolates this directly; rendering belongs here."""
        return self.value


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


def _parse_hostname(value: Any) -> str:
    """Normalises, then admits. A `BeforeValidator`, so the order is guaranteed.

    The order is the whole point, and it is why this is a function rather than
    `StringConstraints(strip_whitespace=True, to_lower=True, pattern=...)`:
    pydantic applies `pattern` to the *original* string and only then transforms
    it, so the declarative spelling rejects ` ABC.trycloudflare.com ` -- a value
    the normalising form accepts. Verified, not assumed.

    It also keeps the domain's own error text. `String should match pattern
    '^[a-z0-9]+(?:-...'` is a true statement about a regex; this one tells a
    reader what kind of thing was expected, which is the only debugging channel
    a job log offers.
    """
    if not isinstance(value, str):
        raise ValueError(f"not a valid quick-tunnel hostname: {value!r}")
    candidate = value.strip().lower()
    if not _HOSTNAME_PATTERN.match(candidate):
        raise ValueError(f"not a valid quick-tunnel hostname: {value!r}")
    return candidate


# Wherever this alias appears -- a field, a decoded payload, a bare annotation --
# the refinement above runs. That is what makes it a type rather than a habit.
QuickTunnelHost = Annotated[str, BeforeValidator(_parse_hostname)]


@pydantic_dataclass(frozen=True)
class Hostname:
    """A validated quick-tunnel hostname.

    Nominal on purpose. `Annotated[str, ...]` would have been fewer lines, but it
    is still `str` to the type checker, so a raw string would substitute for a
    validated one anywhere a `Hostname` is expected -- which is exactly the
    confusion the refinement exists to prevent.

    Validation now runs inside `__init__`, so `Hostname(x)` and `Hostname.parse(x)`
    cannot disagree. They previously did: `parse` normalised before matching
    while the constructor matched the raw string, so `Hostname(" A.trycloudflare.com ")`
    raised on a value `parse` accepted and returned.
    """

    value: QuickTunnelHost

    @classmethod
    def parse(cls, raw: str) -> Self | None:
        """The total form: absence rather than an exception for expected input."""
        try:
            return cls(raw)
        except ValidationError:
            return None

    def __str__(self) -> str:
        return self.value


# --- tasks -----------------------------------------------------------------


# `strict` is doing real work, not tightening for its own sake. Under pydantic's
# default lax mode `"3"` decodes to `3` and `True` decodes to `1`, so a peer
# could hand over a task whose retry budget was a boolean and be believed. The
# hand-written ladder this replaces tested `isinstance(x, bool)` explicitly for
# exactly that reason; `strict` is that check, declared once per field instead of
# remembered once per field.
_Text = Annotated[str, Field(strict=True)]
_NonEmptyText = Annotated[str, Field(strict=True, min_length=1)]
_Integer = Annotated[int, Field(strict=True)]


@pydantic_dataclass(frozen=True)
class Task:
    """A self-describing unit of build work.

    Carries its own retry budget so it can be handed between machines without
    reference to any external state. That closure property is what makes a steal
    safe: the receiving worker needs nothing from the sender but the task.

    The field types *are* the wire schema. A task crosses the network between
    workers, so encoder and decoder are derived from this one declaration and
    cannot drift into disagreeing about what a task is.
    """

    image: _NonEmptyText
    dockerfile: _NonEmptyText
    # Empty is legal here and nowhere else: a Dockerfile at the repository root
    # has "." for a context, and `relative_to` renders that as an empty string.
    context: _Text
    platform: Platform
    max_retries: _Integer

    @classmethod
    def parse(cls, payload: Any) -> Task | None:
        """Admits an untrusted JSON object into the domain, or rejects it.

        Total by construction: a malformed task from a peer becomes `None` and
        is filtered out, never an exception that would take down the steal that
        was carrying it. A non-object payload is rejected too -- the decoder
        checks the shape, so this no longer depends on remembering to.
        """
        try:
            return _TASK.validate_python(payload)
        except ValidationError:
            return None

    def as_json(self) -> dict[str, Any]:
        """The wire form, produced by the same schema that reads it back."""
        # `dump_python` is typed `Any`; the annotation is what pins the shape
        # this function promises rather than passing that `Any` on to callers.
        encoded: dict[str, Any] = _TASK.dump_python(self, mode="json")
        return encoded


# Built once. A TypeAdapter compiles a validator, so constructing one per call
# would put that cost on every task in every steal.
_TASK: TypeAdapter[Task] = TypeAdapter(Task)


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
    # Mapping, not dict: `frozen=True` protects the reference, never the object
    # it points at, so a `dict` field on a frozen record is a mutable value
    # wearing an immutable label. Declaring the read-only interface is what
    # makes the promise checkable -- a caller that mutates these diagnostics
    # now fails to type-check instead of quietly editing another thread's
    # failure report.
    metrics: Mapping[str, str]
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


# --- pinned assets ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Installed:
    path: Path
    version: str


@dataclass(frozen=True, slots=True)
class InstallFailed:
    reason: str


# Shared by cloudflared and mihomo. Both are pinned third-party executables
# fetched, digest-checked, and made runnable by the same contract, so they share
# one outcome type rather than two structurally identical ones under different
# names. It lives here, with every other closed sum, so neither provisioning
# module has to import the other to name its own result.
InstallOutcome = Installed | InstallFailed


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
