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
from dataclasses import dataclass, field
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
    def parse(cls, raw: str) -> Self | None:
        """The total form: absence rather than an exception for expected input.

        A batch id read back from a registry label is untrusted -- it was written
        by some earlier run of unknown vintage, or by hand -- so it crosses into
        the domain here rather than being believed because of where it was found.
        """
        try:
            return cls(raw.strip())
        except ValueError:
            return None

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


def selector(batch: BatchId | None) -> str:
    """Renders the batch suffix that sharpens a floating tag into a pinned one.

    `code-server-base` names whatever that tag points at now;
    `code-server-base.{batch}` names one generation of it and nothing else. The
    suffix is optional at every call site, which is what lets a build degrade to
    the floating tag rather than fail when a generation could not be resolved.

    Absence renders as the empty string, so the same code path produces both
    forms and no caller needs a branch for the degraded one.

    The dot lives here, in the rendering, and never in the value. That is what
    makes concatenation injective: a batch id is drawn from `_BATCH_ALPHABET`,
    which contains no dot, so `image` + `.batch` can be read apart again with no
    escaping and no ambiguity about where the name ends. It is the same law as
    the separator in `derive.material`, one level down.
    """
    return "" if batch is None else f".{batch}"


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


class Usage(StrEnum):
    """How one image of this repository consumes another.

    The distinction is what makes provenance diagnostic rather than decorative.
    A BASE edge says this image's shared libraries come from there. An ARTIFACT
    edge says binaries built against *that* image's libraries were copied in to
    run against these. When the two edges of one image resolve to different
    batches and an upstream release bumped in between, that is the mismatch --
    and it is invisible unless the edges are distinguished.
    """

    BASE = "base"
    ARTIFACT = "artifact"


@pydantic_dataclass(frozen=True)
class Dependency:
    """One edge from an image to another image this repository builds.

    Ordered by (image, usage) wherever a collection of these is produced, so the
    labels rendered from them are byte-stable across runs. An unordered set would
    make every rebuild a digest change for a graph that had not moved.
    """

    image: _NonEmptyText
    usage: Usage
    # The build argument this file expresses the reference through, recorded from
    # the declaration rather than derived from the image name.
    #
    # Deriving it was the original design and every defect in it descended from
    # writing one value down twice: a folding rule, an ASCII hazard, a collision
    # check across the tree, and a whole class of "carries the wrong selector".
    # The parser reads the name off the same line it reads the image from, so
    # nothing has to connect them and none of that machinery has to exist.
    #
    # Required, because an edge without one cannot be pinned: the build would
    # emit `--build-arg =reference` and BuildKit would take it as a nameless
    # argument. A default of "" declared it non-empty and then supplied the one
    # value the constraint forbids, since pydantic does not validate defaults.
    argument: _NonEmptyText
    # How many generations back this edge must reach to stay coherent, which is
    # the difference in graph level between the image declaring it and the image
    # it names.
    #
    # An artifact image's content is fixed when it is built: its binaries were
    # compiled against a base one generation older than its own batch. So an
    # image cannot sit on the same generation it copies artifacts from -- the
    # base has to be one older still, or the binaries land on libraries they were
    # not linked against. Level difference is exactly that offset, which is what
    # makes a single depth-indexed rule cover the whole graph rather than each
    # consumer needing its own unification.
    #
    # Defaults to one: a direct dependency of an image with no deeper chain.
    generations_back: _Integer = 1

    def sort_key(self) -> tuple[str, str]:
        return (self.image, str(self.usage))


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
    # Both directions of this image's place in the repository's own dependency
    # graph, and both carried rather than looked up: a stolen task must still
    # know them, which is the same closure argument the retry budget is here for.
    #
    # They are not symmetric in use. `dependencies` is resolved against the
    # registry and published as a label; `dependents` is never published -- who
    # consumes an image is recoverable from any checkout -- and exists only to
    # decide whether this image is labelled at all.
    #
    # Defaulted because most images are isolated, and an empty pair is precisely
    # how the provenance labels know to stay off an image whose digest would
    # otherwise churn every run for no information. See
    # `provenance.label_arguments`.
    dependencies: tuple[Dependency, ...] = ()
    dependents: tuple[_NonEmptyText, ...] = ()

    @property
    def labelled(self) -> bool:
        """Whether this image carries provenance labels.

        Membership in the repository's own graph, in either direction, and the
        rule is stated once here because it has two readings that must not drift.
        An image nothing consumes gains nothing from a batch label; an image
        something consumes must carry one whether or not it has dependencies of
        its own, or its consumers have nothing to read off it.

        The negative case is the one that costs something. A label is part of the
        image configuration, so labelling all thirty images to describe the eight
        with edges would change every digest on every run -- which is exactly
        what pinning SOURCE_DATE_EPOCH to the start of the month exists to avoid.
        """
        return bool(self.dependencies or self.dependents)

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


# --- what one dependency edge resolved to -----------------------------------


@dataclass(frozen=True, slots=True)
class Minted:
    """The dependency carries a batch label: the group it came from is known."""

    batch: BatchId
    digest: str
    # What *this* image recorded consuming, read back off its own `consumes`
    # label, keyed by image name and holding only the edges it managed to pin.
    #
    # One level of indirection that the batch alone cannot give. `batch` says
    # which generation this dependency belongs to; `built_on` says which
    # generation its *contents* were compiled against. The difference between
    # those two is the skew this whole mechanism exists to find, and comparing
    # batches alone can never show it -- every floating tag in a run carries the
    # same batch, so they always agree.
    built_on: Mapping[str, BatchId] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class ResolvedEdge:
    """One edge of the graph paired with what the registry said about it.

    A product rather than a `Mapping[str, Provenance]` keyed by image name. The
    mapping made every reader look a dependency up by string and decide what an
    absent key meant, which is a lookup that cannot miss in a design where every
    edge is resolved exactly once -- so the "not resolved" branch each caller
    carried was unreachable prose. Pairing them makes the absence unrepresentable
    and the report a straight render.
    """

    dependency: Dependency
    provenance: Provenance
    # The reference the build is handed for this edge, and the one the registry
    # was asked about. Carried rather than recomputed because the two must be the
    # same string: a `consumes` label describing a floating tag while the build
    # ran against a pinned one is a record of an image that was never consumed,
    # and every reader downstream -- the skew check first among them -- believes
    # that record.
    reference: str


# --- build outcomes --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildSucceeded:
    task: Task
    attempts: int
    duration_seconds: float
    # What each of this task's dependency edges resolved to, carried out of the
    # build so the job summary can report it. Empty for the images with no edges,
    # which is most of them.
    edges: tuple[ResolvedEdge, ...] = ()
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
    # Carried on a failure too: what a build was assembled from is most worth
    # reading when it did not work.
    edges: tuple[ResolvedEdge, ...] = ()
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
    """The peer holds nothing it could ever hand over.

    Not the same as an empty queue, and the difference is what a thief is
    actually asking about. A victim retains its last task rather than stripping
    itself idle, so a peer holding exactly one will refuse every steal for the
    rest of its life; reporting it as busy left the thief polling a peer that
    had already given its final answer. What makes stopping on this sound is
    that a queue only ever shrinks -- work moves between workers by stealing,
    and a peer with nothing spare has nothing to move -- so a peer that is
    drained once is drained for good.
    """


@dataclass(frozen=True, slots=True)
class Working:
    """The peer holds tasks beyond the one it keeps for itself."""

    spare: int


@dataclass(frozen=True, slots=True)
class HealthUnknown:
    reason: str


# Only Drained is evidence that a peer is finished. HealthUnknown must never be
# read as "done" on its own, which is precisely the confusion that would let a
# worker exit while a late-booting peer still holds tasks -- see
# `MeshClient.peers_drained`, which admits it only for a peer it has reached
# before and so knows to have shut down rather than not yet started.
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
