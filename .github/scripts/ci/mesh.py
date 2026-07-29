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


# --- wire protocol ---------------------------------------------------------


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Authenticates the timestamp and body together.

    Signing both under one MAC is what stops a valid signature being lifted onto
    a different body or replayed with a fresh timestamp.
    """
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(timestamp.encode())
    mac.update(b"\n")
    mac.update(body)
    return mac.hexdigest()


def verify(
    secret: str,
    timestamp: str,
    presented: str,
    body: bytes,
    now: float,
    max_skew_seconds: float = MAX_CLOCK_SKEW_SECONDS,
) -> AuthOutcome:
    """Checks a request's signature. Pure, so every rejection path is testable.

    Returns a reason rather than a bare failure: on a security boundary, "clock
    skew" and "bad signature" call for very different responses from whoever
    reads the log.
    """
    try:
        skew = abs(now - float(timestamp))
    except ValueError:
        return Rejected("malformed timestamp")

    if skew > max_skew_seconds:
        return Rejected(f"timestamp skew {skew:.0f}s exceeds {max_skew_seconds:.0f}s")

    if not hmac.compare_digest(presented, sign(secret, timestamp, body)):
        return Rejected("signature mismatch")

    return Authenticated(body)


# --- endpoint --------------------------------------------------------------


class _MeshHandler(BaseHTTPRequestHandler):
    """Serves /health and /steal. Concrete state is injected by serve_mesh."""

    secret: str = ""
    worker_id: int = -1
    queue: TaskQueue

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Route through the module logger at debug level so peer chatter does
        # not drown out build output on stderr.
        logger.debug("mesh http: " + fmt, *args)

    def _authenticate(self) -> AuthOutcome:
        import time

        length = int(self.headers.get("Content-Length", "0") or "0")
        return verify(
            secret=self.secret,
            timestamp=self.headers.get("X-Mesh-Ts", ""),
            presented=self.headers.get("X-Mesh-Auth", ""),
            body=self.rfile.read(length) if length else b"",
            now=time.time(),
        )

    def _respond(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _serve(self, route: str, handle: Any) -> None:
        if self.path != route:
            self._respond(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        match self._authenticate():
            case Authenticated(body):
                self._respond(HTTPStatus.OK, handle(body))
            case Rejected(reason):
                logger.warning("Rejected a mesh request: %s", reason)
                self._respond(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            case other:
                assert_never(other)

    def do_GET(self) -> None:
        self._serve("/health", lambda _: {"worker_id": self.worker_id, "pending": len(self.queue)})

    def do_POST(self) -> None:
        self._serve("/steal", self._release)

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
def serve_mesh(worker_id: int, secret: str, queue: TaskQueue) -> Iterator[int]:
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


def derive_run_key(repository_secret: str, run_id: str) -> str:
    """Derives a per-run mesh key from the long-lived repository secret.

    Every worker computes this independently from values it already has, so the
    key never travels through a job output -- which is what broke the previous
    design, since GitHub scrubs masked values out of outputs entirely.
    """
    return sign(repository_secret, run_id, b"mesh-key-v1")


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
        secret: str,
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
                f"{self._peer_origin(hostname)}/steal", content=body, headers=self._headers(body)
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
            response = self._peers.get(f"{origin}/health", headers=self._headers(b""))
            response.raise_for_status()
            pending = int(response.json().get("pending", 0))
        except (httpx.HTTPError, ValueError, TypeError) as error:
            return HealthUnknown(str(error))
        return Drained() if pending == 0 else Working(pending)

    def _headers(self, body: bytes) -> Mapping[str, str]:
        import time

        timestamp = f"{time.time():.3f}"
        return {
            "Content-Type": "application/json",
            "X-Mesh-Ts": timestamp,
            "X-Mesh-Auth": sign(self.secret, timestamp, body),
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
