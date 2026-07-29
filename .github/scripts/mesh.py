#!/usr/bin/env python3

"""
Ad-hoc peer-to-peer mesh for work stealing between GitHub Actions runners.

Each runner serves a small authenticated HTTP endpoint over a TryCloudflare
quick tunnel and publishes its hostname as a git ref, which peers read back to
discover each other. The mesh is strictly an optimisation: every steal path is
non-blocking and failure-tolerant, so a runner that cannot reach any peer simply
builds the tasks it was dealt. Correctness never depends on the mesh working.

Rendezvous note: a TryCloudflare hostname is made of lowercase alphanumerics and
hyphens separated by dots, all of which are legal in a git ref path component.
That lets the hostname live in the ref *name*, so publishing is one POST and
discovery is one GET, with no blobs, trees, or artifacts involved.
"""


import hashlib
import hmac
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

logger = logging.getLogger("mesh")

# Reject authenticated requests whose timestamp drifts beyond this, so a captured
# request cannot be replayed for the remainder of the run.
MAX_CLOCK_SKEW_SECONDS = 120

CLOUDFLARED_URL_PATTERN = re.compile(r"https://([a-z0-9-]+(?:\.[a-z0-9-]+)+)\.trycloudflare\.com")


@dataclass(frozen=True)
class Task:
    """A self-describing unit of build work.

    Carries everything needed to execute it, including its own retry budget, so
    a task can be handed between machines without reference to any external
    state. This is what makes stealing safe: the receiving worker needs nothing
    from the sender beyond the task itself.
    """

    image: str
    dockerfile: str
    context: str
    platform: str
    max_retries: int

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "Task":
        return Task(
            image=payload["image"],
            dockerfile=payload["dockerfile"],
            context=payload["context"],
            platform=payload["platform"],
            max_retries=int(payload["max_retries"]),
        )


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Computes the HMAC a peer must present to be served."""
    mac = hmac.new(secret.encode(), digestmod=hashlib.sha256)
    mac.update(timestamp.encode())
    mac.update(b"\n")
    mac.update(body)
    return mac.hexdigest()


class TaskQueue:
    """A worker's local deque of pending tasks.

    Local builds pop from the head; peers steal from the tail. Taking from
    opposite ends keeps a thief and the victim's own build threads off the same
    entry in the common case, and hands the thief the work least likely to be
    started imminently.
    """

    def __init__(self, tasks: list[Task]) -> None:
        self._tasks: deque[Task] = deque(tasks)
        self._lock = threading.Lock()

    def take_local(self) -> Task | None:
        with self._lock:
            return self._tasks.popleft() if self._tasks else None

    def give_away(self, count: int) -> list[Task]:
        """Releases up to `count` tasks from the tail for a stealing peer.

        Always retains at least one task, so a victim never strips itself idle
        to satisfy a thief that may be about to become busy anyway.
        """
        with self._lock:
            available = max(0, len(self._tasks) - 1)
            taken = min(count, available)
            return [self._tasks.pop() for _ in range(taken)]

    def return_task(self, task: Task) -> None:
        """Puts a task back after a failed handoff, so nothing is lost mid-steal."""
        with self._lock:
            self._tasks.appendleft(task)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)


class _MeshHandler(BaseHTTPRequestHandler):
    """Serves /health and /steal. Wired up by MeshServer, which injects state."""

    secret: str = ""
    worker_id: int = -1
    queue: TaskQueue | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        # BaseHTTPRequestHandler logs to stderr by default; route through our
        # logger at debug level so peer chatter does not drown out build output.
        logger.debug("mesh http: " + fmt, *args)

    def _authenticated_body(self) -> bytes | None:
        """Returns the request body if the HMAC and timestamp check out."""
        timestamp = self.headers.get("X-Mesh-Ts", "")
        presented = self.headers.get("X-Mesh-Auth", "")
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else b""

        try:
            skew = abs(time.time() - float(timestamp))
        except ValueError:
            return None
        if skew > MAX_CLOCK_SKEW_SECONDS:
            return None

        if not hmac.compare_digest(presented, sign(self.secret, timestamp, body)):
            return None
        return body

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._respond(404, {"error": "not found"})
            return
        if self._authenticated_body() is None:
            self._respond(401, {"error": "unauthorized"})
            return
        assert self.queue is not None
        self._respond(200, {"worker_id": self.worker_id, "pending": len(self.queue)})

    def do_POST(self) -> None:
        if self.path != "/steal":
            self._respond(404, {"error": "not found"})
            return

        body = self._authenticated_body()
        if body is None:
            self._respond(401, {"error": "unauthorized"})
            return

        try:
            count = int(json.loads(body or b"{}").get("count", 1))
        except (ValueError, AttributeError):
            count = 1

        assert self.queue is not None
        released = self.queue.give_away(max(1, count))
        if released:
            logger.info(
                "Released %d task(s) to a peer: %s",
                len(released),
                ", ".join(task.image for task in released),
            )
        self._respond(200, {"tasks": [asdict(task) for task in released]})


class MeshServer:
    """Runs the local endpoint and the cloudflared quick tunnel in front of it."""

    def __init__(self, worker_id: int, secret: str, queue: TaskQueue) -> None:
        self.worker_id = worker_id
        self.secret = secret
        self.queue = queue
        self.public_hostname: str | None = None
        self._httpd: HTTPServer | None = None
        self._tunnel: subprocess.Popen | None = None

    def _free_port(self) -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def start(self, tunnel_timeout_seconds: float = 60.0) -> str | None:
        """Starts the server and tunnel. Returns the hostname, or None on failure.

        A None return is survivable by design: the worker keeps its own tasks and
        simply never receives steal requests.
        """
        port = self._free_port()

        handler = type(
            "_BoundMeshHandler",
            (_MeshHandler,),
            {"secret": self.secret, "worker_id": self.worker_id, "queue": self.queue},
        )
        self._httpd = HTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        logger.info("Mesh endpoint listening on 127.0.0.1:%d", port)

        try:
            # stdout/stderr are piped rather than inherited: on a public repo the
            # workflow log is world-readable in real time, and cloudflared prints
            # the tunnel URL on startup. Capturing it keeps the hostname out of
            # the log, leaving the HMAC as the second line of defence rather than
            # the only one.
            self._tunnel = subprocess.Popen(
                [
                    "cloudflared",
                    "tunnel",
                    "--url",
                    f"http://127.0.0.1:{port}",
                    "--no-autoupdate",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            logger.warning("cloudflared not installed; running without a mesh endpoint")
            return None

        hostname = self._read_hostname(deadline=time.time() + tunnel_timeout_seconds)
        if hostname is None:
            logger.warning("Tunnel did not report a hostname in time; continuing solo")
            return None

        self.public_hostname = hostname
        logger.info("Mesh endpoint published (hostname withheld from logs)")
        return hostname

    def _read_hostname(self, deadline: float) -> str | None:
        """Scrapes the quick-tunnel hostname out of cloudflared's startup output."""
        assert self._tunnel is not None and self._tunnel.stdout is not None

        found: list[str] = []

        def scan() -> None:
            assert self._tunnel is not None and self._tunnel.stdout is not None
            for line in self._tunnel.stdout:
                match = CLOUDFLARED_URL_PATTERN.search(line)
                if match and not found:
                    found.append(f"{match.group(1)}.trycloudflare.com")
                    # Keep draining afterwards so the pipe never fills and blocks
                    # cloudflared, but never echo the contents anywhere.

        threading.Thread(target=scan, daemon=True).start()

        while time.time() < deadline:
            if found:
                return found[0]
            if self._tunnel.poll() is not None:
                return None
            time.sleep(0.25)
        return None

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
        if self._tunnel is not None:
            self._tunnel.terminate()


class MeshClient:
    """Discovers peers via git refs and issues non-blocking steal requests."""

    def __init__(
        self,
        secret: str,
        worker_id: int,
        repository: str,
        run_id: str,
        platform: str,
        token: str,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self.secret = secret
        self.worker_id = worker_id
        self.repository = repository
        self.run_id = run_id
        self.platform = platform
        self.token = token
        self.timeout = request_timeout_seconds
        self._ref_prefix = f"mesh/{run_id}/{platform}"

    # -- rendezvous ---------------------------------------------------------

    def publish(self, hostname: str, commit_sha: str) -> bool:
        """Announces this worker's tunnel hostname by creating a git ref.

        The hostname is carried in the ref name itself, so no object needs to be
        written to hold it. The ref target is just the run's commit.
        """
        ref = f"refs/{self._ref_prefix}/{self.worker_id}/{hostname}"
        try:
            self._github(
                "POST",
                f"/repos/{self.repository}/git/refs",
                {"ref": ref, "sha": commit_sha},
            )
            return True
        except urllib.error.HTTPError as error:
            logger.warning("Could not publish mesh ref (%s); continuing solo", error.code)
            return False
        except Exception as error:
            logger.warning("Could not publish mesh ref (%s); continuing solo", error)
            return False

    def discover_peers(self) -> dict[int, str]:
        """Returns {worker_id: hostname} for every peer that has published.

        One request returns the whole membership set. Peers that have not booted
        yet are simply absent, which is why stealing must tolerate an incomplete
        view rather than treating it as "no work remains".
        """
        try:
            refs = self._github(
                "GET", f"/repos/{self.repository}/git/matching-refs/{self._ref_prefix}"
            )
        except Exception as error:
            logger.debug("Peer discovery failed (%s)", error)
            return {}

        peers: dict[int, str] = {}
        for entry in refs or []:
            parts = entry.get("ref", "").split("/")
            # refs/mesh/<run_id>/<platform>/<worker_id>/<hostname>
            if len(parts) < 6:
                continue
            try:
                peer_id = int(parts[-2])
            except ValueError:
                continue
            if peer_id != self.worker_id:
                peers[peer_id] = parts[-1]
        return peers

    def _github(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        request = urllib.request.Request(
            f"https://api.github.com{path}",
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    # -- stealing -----------------------------------------------------------

    def steal_from(self, hostname: str, count: int = 1) -> list[Task]:
        """Asks one peer for work. Returns [] on any failure, never raises.

        Every failure mode here -- peer not yet up, tunnel dead, request timed
        out, peer genuinely empty -- collapses to the same answer, because the
        caller's response is identical in all four cases: try someone else, then
        get on with your own queue.
        """
        body = json.dumps({"count": count}).encode()
        timestamp = f"{time.time():.3f}"
        request = urllib.request.Request(
            f"https://{hostname}/steal",
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Mesh-Ts": timestamp,
                "X-Mesh-Auth": sign(self.secret, timestamp, body),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read() or b"{}")
        except Exception as error:
            logger.debug("Steal attempt failed (%s)", error)
            return []

        return [Task.from_dict(entry) for entry in payload.get("tasks", [])]

    def peer_pending(self, hostname: str) -> int | None:
        """Returns a peer's pending count, or None if it cannot be reached.

        The distinction matters: zero means 'confirmed done', while None means
        'unknown', and only the former is safe to treat as grounds for exiting.
        """
        timestamp = f"{time.time():.3f}"
        request = urllib.request.Request(
            f"https://{hostname}/health",
            method="GET",
            headers={
                "X-Mesh-Ts": timestamp,
                "X-Mesh-Auth": sign(self.secret, timestamp, b""),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return int(json.loads(response.read() or b"{}").get("pending", 0))
        except Exception:
            return None

    def all_peers_drained(self, peers: dict[int, str], expected_peers: int) -> bool:
        """True only when every peer we expect to exist is reachable and empty.

        A peer that has not published yet, or that we cannot reach, keeps this
        False -- so an incomplete view of the mesh never gets mistaken for the
        work being finished.
        """
        if len(peers) < expected_peers:
            return False
        return all(self.peer_pending(hostname) == 0 for hostname in peers.values())

    def steal_round(self, peers: dict[int, str], count: int = 1) -> list[Task]:
        """Tries each known peer once, in an order that varies per worker.

        Starting every worker at a different offset stops all thieves from
        converging on the same victim.
        """
        if not peers:
            return []
        ordered = sorted(peers.items())
        offset = self.worker_id % len(ordered)
        for peer_id, hostname in ordered[offset:] + ordered[:offset]:
            stolen = self.steal_from(hostname, count=count)
            if stolen:
                logger.info(
                    "Stole %d task(s) from worker %d: %s",
                    len(stolen),
                    peer_id,
                    ", ".join(task.image for task in stolen),
                )
                return stolen
        return []


def run_worker(
    queue: TaskQueue,
    client: MeshClient,
    execute: Callable[[Task], bool],
    slots: int,
    expected_peers: int = 0,
    idle_grace_seconds: float = 90.0,
    idle_poll_seconds: float = 3.0,
) -> list[tuple[Task, bool]]:
    """Drains the local queue with `slots` concurrent builds, stealing when idle.

    Threads rather than processes: each build is a blocking subprocess call, so
    the GIL is released for essentially the whole task, and threads let the build
    slots and the mesh server share one queue without a Manager.

    Returns (task, succeeded) for everything this worker executed.
    """
    results: list[tuple[Task, bool]] = []
    results_lock = threading.Lock()
    peers: dict[int, str] = {}
    peers_lock = threading.Lock()

    def refresh_peers() -> dict[int, str]:
        # Re-read membership on every idle transition rather than caching once:
        # a worker that boots late is invisible to an early poll, and that stale
        # view is exactly what would otherwise cause a premature exit.
        with peers_lock:
            peers.update(client.discover_peers())
            return dict(peers)

    def slot(slot_index: int) -> None:
        idle_since: float | None = None
        while True:
            task = queue.take_local()

            if task is None:
                stolen = client.steal_round(refresh_peers(), count=1)
                if stolen:
                    idle_since = None
                    task = stolen[0]
                    for extra in stolen[1:]:
                        queue.return_task(extra)
                else:
                    current_peers = refresh_peers()

                    # Fast path: if every peer we expect is reachable and reports
                    # an empty queue, the run really is finished and there is no
                    # reason to sit out the grace period.
                    if client.all_peers_drained(current_peers, expected_peers):
                        logger.info("Slot %d: all peers drained; stopping", slot_index)
                        return

                    # Otherwise a peer may still be booting, or briefly
                    # unreachable, so wait before concluding anything. Exiting
                    # early costs a missed steal, never a missed build: an
                    # unstolen task stays with whoever was dealt it.
                    now = time.time()
                    if idle_since is None:
                        idle_since = now
                    elif now - idle_since > idle_grace_seconds:
                        logger.info(
                            "Slot %d idle for %.0fs with no reachable work; stopping",
                            slot_index,
                            idle_grace_seconds,
                        )
                        return
                    time.sleep(idle_poll_seconds)
                    continue

            # A task that raises outside its own retry loop must not take the
            # slot down with it: the slot has peers' stolen work to get through,
            # and a dead slot would silently reduce this worker's capacity.
            try:
                succeeded = execute(task)
            except Exception:
                logger.exception("Slot %d: unhandled error building %s", slot_index, task.image)
                succeeded = False

            with results_lock:
                results.append((task, succeeded))

    threads = [
        threading.Thread(target=slot, args=(index,), name=f"slot-{index}")
        for index in range(slots)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return results
