"""The peer-to-peer build mesh: wire protocol, endpoint, and client.

Rendezvous design: a validated quick-tunnel hostname is a legal git ref path
component, so a worker publishes itself by creating
`refs/mesh/<run>/<platform>/<worker>/<hostname>`. The hostname lives in the ref
*name*, which makes publishing one POST and discovery one matching-refs GET,
with no blob, tree, or artifact needed to carry the payload.

Nothing here is load-bearing for correctness. Every failure -- unreachable peer,
rejected signature, dead tunnel, unpublished ref -- degrades the run to plain
static partitioning, in which each worker simply builds the share it was dealt.
Reconciliation against the registry is what turns that into a guarantee.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import socket
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, assert_never

import httpx

from ci.domain import (
    Authenticated,
    AuthOutcome,
    Drained,
    HeaderAuthOutcome,
    HeadersAuthentic,
    HealthUnknown,
    Hostname,
    PeerEmpty,
    PeerHealth,
    PeerUnreachable,
    Platform,
    Rejected,
    StealOutcome,
    Stolen,
    Task,
    Working,
)
from ci.scheduling import TaskQueue

logger = logging.getLogger("ci.mesh")

# Bounds how long a captured request stays replayable.
MAX_CLOCK_SKEW_SECONDS = 120.0

# Generous headroom for protocol growth -- a task descriptor is ~150 bytes, so
# this accommodates tens of thousands of them. It is safe to set this high
# because a body is only ever read after its signature has been verified from
# the headers, so the allocation is on behalf of a peer that already holds the
# key. The declared length is itself covered by that signature.
#
# There is deliberately no cap on concurrent requests. One was tried and
# removed: because authentication now happens first, such a cap could only ever
# throttle legitimate peers, while an attacker holding the key could drain the
# queue outright and one without it never reaches the slot at all. A limit only
# the defender can hit is a self-inflicted denial of service.
MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024

# Drops connections that stall mid-request. Without it a peer that opens a
# socket and never finishes holds a server thread for the rest of the run.
REQUEST_TIMEOUT_SECONDS = 30.0


# --- wire protocol ---------------------------------------------------------

# BLAKE2b personalisation strings, giving domain separation natively: the same
# key cannot produce a valid tag in the wrong context, because the scope is
# mixed into the compression function rather than prepended to the message and
# hoped for. Each must be at most blake2b.PERSON_SIZE (16) bytes.
KEY_SCOPE = b"mesh-key-v1"
REQUEST_SCOPE = b"mesh-req-v1"

_MAX_BLAKE2B_KEY = hashlib.blake2b.MAX_KEY_SIZE


def _as_key(material: bytes) -> bytes:
    """Fits arbitrary key material into BLAKE2b's 64-byte key limit.

    HMAC pre-hashes an oversized key silently; BLAKE2b raises instead, so the
    reduction is explicit here. An arbitrarily long MESH_SECRET is therefore
    still valid -- it simply gets compressed, exactly as HMAC would have done.
    """
    if len(material) <= _MAX_BLAKE2B_KEY:
        return material
    return hashlib.blake2b(material, digest_size=_MAX_BLAKE2B_KEY).digest()


def derive_run_key(repository_secret: str, run_id: str, run_attempt: str = "1") -> bytes:
    """Derives this execution's mesh key from the long-lived repository secret.

    Every worker computes it independently from values it already holds, so the
    key never passes through a job output -- which is what broke an earlier
    design, since GitHub scrubs masked values out of outputs entirely. Only
    32-byte tags derived from this key ever cross the wire, and recovering the
    repository secret from one would mean key recovery against keyed BLAKE2b.

    The attempt number is mixed in because GITHUB_RUN_ID is stable across
    re-runs while only GITHUB_RUN_ATTEMPT increments; without it, "re-run failed
    jobs" would reuse a key that may already have been exposed.

    Scoped to KEY_SCOPE so a derivation tag can never be replayed as a request
    signature, even though both use one key and one primitive.
    """
    return hashlib.blake2b(
        f"{run_id}/{run_attempt}".encode(),
        key=_as_key(repository_secret.encode()),
        person=KEY_SCOPE,
        digest_size=32,
    ).digest()


def body_digest(body: bytes) -> str:
    """Digests a body so a signature can commit to it without it being read."""
    return hashlib.blake2b(body, digest_size=32).hexdigest()


def canonical_request(
    method: str, path: str, timestamp: str, content_length: int, digest: str
) -> bytes:
    """The exact bytes a signature commits to.

    Method and path are included deliberately. Without them, a captured
    credential for the read-only /health endpoint verified unchanged against the
    destructive /steal endpoint: both carry an empty body, and the signature
    covered only the timestamp and body.

    The length is covered too, so a receiver may trust it enough to allocate
    against before it has seen a single byte of the body.
    """
    return "\n".join(
        (method.upper(), path, timestamp, str(content_length), digest)
    ).encode()


def sign_request(
    key: bytes, method: str, path: str, timestamp: str, content_length: int, digest: str
) -> str:
    """Tags a request. Keyed BLAKE2b is a MAC by construction; no HMAC wrapper."""
    return hashlib.blake2b(
        canonical_request(method, path, timestamp, content_length, digest),
        key=key,
        person=REQUEST_SCOPE,
        digest_size=32,
    ).hexdigest()


def verify_headers(
    key: bytes,
    method: str,
    path: str,
    timestamp: str,
    declared_length: str,
    digest: str,
    presented: str,
    now: float,
    max_skew_seconds: float = MAX_CLOCK_SKEW_SECONDS,
    max_body_bytes: int = MAX_REQUEST_BODY_BYTES,
) -> HeaderAuthOutcome:
    """Authenticates a request from its headers alone, before the body is read.

    The ordering is the whole point. Receiving a body is the expensive part of
    serving a request, so an unauthenticated caller must be turned away before
    reaching it -- otherwise this endpoint's capacity is exhaustible by anyone
    who can reach it, and it is publicly discoverable by design: its hostname
    lives in a world-readable git ref.

    Pure, so every rejection path is testable without a socket.
    """
    try:
        length = int(declared_length)
    except ValueError:
        return Rejected(f"malformed Content-Length {declared_length!r}")

    if length < 0 or length > max_body_bytes:
        return Rejected(f"body of {length} bytes exceeds the {max_body_bytes} limit")

    try:
        skew = abs(now - float(timestamp))
    except ValueError:
        return Rejected("malformed timestamp")

    if skew > max_skew_seconds:
        return Rejected(f"timestamp skew {skew:.0f}s exceeds {max_skew_seconds:.0f}s")

    if not hmac.compare_digest(
        presented, sign_request(key, method, path, timestamp, length, digest)
    ):
        return Rejected("signature mismatch")

    return HeadersAuthentic(content_length=length, body_digest=digest)


def verify_body(body: bytes, expected: HeadersAuthentic) -> AuthOutcome:
    """Checks a received body against the digest its signature committed to."""
    if len(body) != expected.content_length:
        return Rejected("request body ended early")
    if not hmac.compare_digest(body_digest(body), expected.body_digest):
        return Rejected("body digest mismatch")
    return Authenticated(body)


# --- endpoint --------------------------------------------------------------


class _MeshHandler(BaseHTTPRequestHandler):
    """Serves /health and /steal. Concrete state is injected by serve_mesh."""

    secret: bytes = b""
    worker_id: int = -1
    queue: TaskQueue

    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT_SECONDS

    def log_message(self, fmt: str, *args: Any) -> None:
        # Route through the module logger at debug level so peer chatter does
        # not drown out build output on stderr.
        logger.debug("mesh http: " + fmt, *args)

    def _authenticate_headers(self, method: str) -> HeaderAuthOutcome:
        import time

        return verify_headers(
            key=self.secret,
            method=method,
            path=self.path,
            timestamp=self.headers.get("X-Mesh-Ts", ""),
            declared_length=self.headers.get("Content-Length", "0") or "0",
            digest=self.headers.get("X-Mesh-Body", ""),
            presented=self.headers.get("X-Mesh-Auth", ""),
            now=time.time(),
        )

    def _respond(
        self, status: HTTPStatus, payload: Mapping[str, Any], close: bool = False
    ) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if close:
            # A rejected request may have left an unread body on the socket, so
            # the connection cannot safely be reused for a following request.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(encoded)

    def _reject(self, reason: str) -> None:
        logger.warning("Rejected a mesh request: %s", reason)
        self._respond(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"}, close=True)

    def _serve(self, method: str, route: str, handle: Any) -> None:
        if self.path != route:
            self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        # Authenticate before anything expensive, and before taking a capacity
        # slot. Taking the slot first would let an attacker who cannot forge a
        # signature still exhaust every slot and silence the mesh -- trading a
        # memory problem for an availability one.
        match self._authenticate_headers(method):
            case Rejected(reason):
                self._reject(reason)
                return
            case HeadersAuthentic() as authentic:
                pass
            case other:
                assert_never(other)

        # Only signed callers reach here, so the read below is work done for a
        # peer that already holds the key.
        body = self.rfile.read(authentic.content_length) if authentic.content_length else b""
        match verify_body(body, authentic):
            case Authenticated(verified):
                self._respond(HTTPStatus.OK, handle(verified))
            case Rejected(reason):
                self._reject(reason)
            case other:
                assert_never(other)

    def do_GET(self) -> None:
        self._serve(
            "GET",
            "/health",
            lambda _: {"worker_id": self.worker_id, "pending": len(self.queue)},
        )

    def do_POST(self) -> None:
        self._serve("POST", "/steal", self._release)

    def _release(self, body: bytes) -> Mapping[str, Any]:
        try:
            requested = int(json.loads(body or b"{}").get("count", 1))
        except (ValueError, TypeError, AttributeError):
            requested = 1

        released = self.queue.release(max(1, requested))
        if released:
            logger.info(
                "Released %d task(s) to a peer: %s",
                len(released),
                ", ".join(task.image for task in released),
            )
        return {"tasks": [task.as_json() for task in released]}


@contextmanager
def serve_mesh(worker_id: int, secret: bytes, queue: TaskQueue) -> Iterator[int]:
    """Serves the mesh endpoint on a free loopback port for the block's duration.

    Threaded so a slow peer cannot block another peer's steal, and scoped so the
    listening socket is always closed -- the previous start/stop pair leaked the
    server whenever the worker raised between the two calls.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    handler = type(
        "_BoundMeshHandler",
        (_MeshHandler,),
        {"secret": secret, "worker_id": worker_id, "queue": queue},
    )
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True

    thread = threading.Thread(target=server.serve_forever, daemon=True, name="mesh-server")
    thread.start()
    logger.info("Mesh endpoint listening on 127.0.0.1:%d", port)

    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()


@dataclass(frozen=True, slots=True)
class SoloMesh:
    """The MeshView used when no mesh credential is configured.

    Reports no peers and, crucially, `peers_drained() is True`: with nobody to
    wait for, a slot that empties its queue should stop immediately rather than
    sit out the grace period. The run degrades to static partitioning, which is
    exactly what dealing disjoint shares was designed to make safe.
    """

    def attempt_steal(self) -> StealOutcome:
        return PeerUnreachable("mesh disabled: no MESH_SECRET configured")

    def peers_drained(self) -> bool:
        return True




# --- client ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rendezvous:
    """Locates this run's mesh in the repository's ref namespace."""

    repository: str
    run_id: str
    platform: Platform

    @property
    def prefix(self) -> str:
        return f"mesh/{self.run_id}/{self.platform}"

    def ref_for(self, worker_id: int, hostname: Hostname) -> str:
        return f"refs/{self.prefix}/{worker_id}/{hostname}"

    def parse_ref(self, ref: str) -> tuple[int, Hostname] | None:
        """Recovers (worker, hostname) from a ref, rejecting anything malformed.

        Refs are read back from a mutable remote namespace, so they are parsed
        rather than trusted: the hostname goes on to become a URL.
        """
        parts = ref.split("/")
        if len(parts) != 6 or not ref.startswith(f"refs/{self.prefix}/"):
            return None
        try:
            worker_id = int(parts[4])
        except ValueError:
            return None
        hostname = Hostname.parse(parts[5])
        return None if hostname is None else (worker_id, hostname)


class MeshClient:
    """Discovers peers and steals from them. Never raises on peer failure."""

    def __init__(
        self,
        secret: bytes,
        worker_id: int,
        rendezvous: Rendezvous,
        github: httpx.Client,
        peers_client: httpx.Client,
        expected_peers: int,
        peer_origin: Callable[[Hostname], str] = lambda hostname: f"https://{hostname}",
    ) -> None:
        self.secret = secret
        self._worker_id = worker_id
        self._rendezvous = rendezvous
        self._github = github
        self._peers = peers_client
        self._expected_peers = expected_peers
        # How a validated hostname becomes an origin. Injectable so the client
        # can be exercised against a plain-HTTP loopback endpoint; in production
        # a quick tunnel is always reached over TLS.
        self._peer_origin = peer_origin
        self._known: dict[int, Hostname] = {}

    def seed_peers(self, peers: Mapping[int, Hostname]) -> None:
        """Injects known membership, bypassing the git-ref rendezvous."""
        self._known.update(peers)

    # -- rendezvous ---------------------------------------------------------

    def publish(self, hostname: Hostname, commit_sha: str) -> bool:
        ref = self._rendezvous.ref_for(self._worker_id, hostname)
        try:
            response = self._github.post(
                f"/repos/{self._rendezvous.repository}/git/refs",
                json={"ref": ref, "sha": commit_sha},
            )
            response.raise_for_status()
            logger.info(
                "Published worker %d to the rendezvous (hostname withheld from logs)",
                self._worker_id,
            )
            return True
        except httpx.HTTPError as error:
            logger.warning("Could not publish mesh ref (%s); continuing solo", error)
            return False

    def discover_peers(self) -> Mapping[int, Hostname]:
        """Re-reads membership, accumulating peers as they appear.

        Membership is re-read on every idle transition rather than cached once:
        a worker that boots late is invisible to an early poll, and acting on
        that stale view is exactly what would cause a premature exit.
        """
        try:
            response = self._github.get(
                f"/repos/{self._rendezvous.repository}"
                f"/git/matching-refs/{self._rendezvous.prefix}"
            )
            response.raise_for_status()
            entries = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.debug("Peer discovery failed (%s)", error)
            return dict(self._known)

        parsed = (self._rendezvous.parse_ref(entry.get("ref", "")) for entry in entries)
        discovered = {
            worker_id: hostname
            for worker_id, hostname in filter(None, parsed)
            if worker_id != self._worker_id
        }

        # Logged by worker id only, never by hostname: the whole point of
        # withholding the hostname is that the workflow log is world-readable on
        # a public repository. Without this line, "every peer was found" and
        # "discovery is silently broken" look identical whenever no worker
        # happens to go idle.
        appeared = sorted(set(discovered) - set(self._known))
        if appeared:
            logger.info(
                "Discovered peer(s) %s; now know %d of %d expected",
                appeared,
                len(discovered),
                self._expected_peers,
            )

        self._known.update(discovered)
        return dict(self._known)

    def cleanup(self) -> int:
        """Deletes this run's mesh refs. Returns how many were removed."""
        try:
            response = self._github.get(
                f"/repos/{self._rendezvous.repository}"
                f"/git/matching-refs/mesh/{self._rendezvous.run_id}"
            )
            response.raise_for_status()
            refs = [entry.get("ref", "") for entry in response.json()]
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Could not list mesh refs for cleanup: %s", error)
            return 0

        removed = 0
        for ref in filter(lambda candidate: candidate.startswith("refs/"), refs):
            try:
                self._github.delete(
                    f"/repos/{self._rendezvous.repository}/git/{ref}"
                ).raise_for_status()
                removed += 1
            except httpx.HTTPError as error:
                logger.warning("Could not delete %s: %s", ref, error)
        return removed

    # -- peer interaction ---------------------------------------------------

    def steal_from(self, hostname: Hostname, count: int = 1) -> StealOutcome:
        body = json.dumps({"count": count}).encode()
        try:
            response = self._peers.post(
                f"{self._peer_origin(hostname)}/steal",
                content=body,
                headers=self._headers("POST", "/steal", body),
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            return PeerUnreachable(str(error))

        parsed = tuple(filter(None, map(Task.parse, payload.get("tasks", []))))
        return Stolen(parsed) if parsed else PeerEmpty()

    def health_of(self, hostname: Hostname) -> PeerHealth:
        try:
            origin = self._peer_origin(hostname)
            response = self._peers.get(
                f"{origin}/health", headers=self._headers("GET", "/health", b"")
            )
            response.raise_for_status()
            pending = int(response.json().get("pending", 0))
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return HealthUnknown(str(error))
        return Drained() if pending == 0 else Working(pending)

    def _headers(self, method: str, path: str, body: bytes) -> Mapping[str, str]:
        import time

        timestamp = f"{time.time():.3f}"
        digest = body_digest(body)
        return {
            "Content-Type": "application/json",
            "X-Mesh-Ts": timestamp,
            "X-Mesh-Body": digest,
            "X-Mesh-Auth": sign_request(
                self.secret, method, path, timestamp, len(body), digest
            ),
        }

    # -- MeshView -----------------------------------------------------------

    def attempt_steal(self) -> StealOutcome:
        """Tries each known peer once, in an order that varies per worker.

        Starting each worker at a different offset stops every thief converging
        on the same victim. Reports Stolen on the first success; otherwise
        reports whether anyone was even reachable, which is the distinction that
        makes a silently broken mesh visible.
        """
        peers = sorted(self.discover_peers().items())
        if not peers:
            return PeerUnreachable("no peers published yet")

        offset = self._worker_id % len(peers)
        reachable = False

        for peer_id, hostname in peers[offset:] + peers[:offset]:
            match self.steal_from(hostname):
                case Stolen(tasks):
                    logger.info(
                        "Stole %d task(s) from worker %d: %s",
                        len(tasks),
                        peer_id,
                        ", ".join(task.image for task in tasks),
                    )
                    return Stolen(tasks)
                case PeerEmpty():
                    reachable = True
                case PeerUnreachable():
                    continue

        return PeerEmpty() if reachable else PeerUnreachable("no peer answered")

    def peers_drained(self) -> bool:
        """True only when every expected peer is reachable and reports empty.

        Requiring the full expected count is what stops an incomplete view being
        mistaken for the work being finished; a peer that has not published yet
        keeps this false, and so does one that cannot be reached.
        """
        peers = self.discover_peers()
        if len(peers) < self._expected_peers:
            return False
        return all(isinstance(self.health_of(hostname), Drained) for hostname in peers.values())
