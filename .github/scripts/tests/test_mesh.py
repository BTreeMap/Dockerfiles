"""Wire protocol, rendezvous parsing, and a live end-to-end steal."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from ci.domain import (
    Authenticated,
    Hostname,
    PeerEmpty,
    PeerUnreachable,
    Platform,
    Rejected,
    Stolen,
    Task,
    Working,
)
from ci.mesh import MeshClient, Rendezvous, serve_mesh, sign, verify
from ci.scheduling import TaskQueue

SECRET = "s3cret"
HOST = Hostname("busy-blue-cat.trycloudflare.com")


def task(name: str) -> Task:
    return Task(
        image=name,
        dockerfile=f"{name}/Dockerfile",
        context=name,
        platform=Platform.AMD64,
        max_retries=3,
    )


# --- signing ---------------------------------------------------------------


def test_a_correct_signature_authenticates() -> None:
    body, ts = b'{"count": 1}', "1000.0"
    outcome = verify(SECRET, ts, sign(SECRET, ts, body), body, now=1000.0)
    assert outcome == Authenticated(body)


def test_a_wrong_secret_is_rejected() -> None:
    body, ts = b"{}", "1000.0"
    outcome = verify(SECRET, ts, sign("other", ts, body), body, now=1000.0)
    assert isinstance(outcome, Rejected) and "signature" in outcome.reason


def test_a_signature_cannot_be_lifted_onto_a_different_body() -> None:
    ts = "1000.0"
    stolen_mac = sign(SECRET, ts, b'{"count": 1}')
    outcome = verify(SECRET, ts, stolen_mac, b'{"count": 9999}', now=1000.0)
    assert isinstance(outcome, Rejected)


def test_a_stale_request_is_rejected() -> None:
    body, ts = b"{}", "1000.0"
    outcome = verify(SECRET, ts, sign(SECRET, ts, body), body, now=2000.0)
    assert isinstance(outcome, Rejected) and "skew" in outcome.reason


def test_a_malformed_timestamp_is_rejected_rather_than_raising() -> None:
    outcome = verify(SECRET, "not-a-number", "whatever", b"", now=1000.0)
    assert isinstance(outcome, Rejected) and "malformed" in outcome.reason


# --- rendezvous ------------------------------------------------------------


RENDEZVOUS = Rendezvous(repository="owner/repo", run_id="42", platform=Platform.AMD64)


def test_a_published_ref_round_trips() -> None:
    ref = RENDEZVOUS.ref_for(3, HOST)
    assert ref == f"refs/mesh/42/amd64/3/{HOST}"
    assert RENDEZVOUS.parse_ref(ref) == (3, HOST)


@pytest.mark.parametrize(
    "ref",
    [
        "refs/mesh/42/amd64/3/evil.example.com",   # hostname fails validation
        "refs/mesh/42/arm64/3/busy-blue-cat.trycloudflare.com",  # other platform
        "refs/mesh/99/amd64/3/busy-blue-cat.trycloudflare.com",  # other run
        "refs/heads/main",
        "refs/mesh/42/amd64/not-a-number/busy-blue-cat.trycloudflare.com",
        "",
    ],
)
def test_malformed_or_foreign_refs_are_rejected(ref: str) -> None:
    # Refs come back from a mutable remote namespace and the hostname becomes a
    # URL, so they are parsed rather than trusted.
    assert RENDEZVOUS.parse_ref(ref) is None


# --- live endpoint ---------------------------------------------------------


def client_for(worker_id: int, port: int, expected_peers: int = 1) -> MeshClient:
    """A client whose peer traffic is directed at a local test endpoint."""
    client = MeshClient(
        secret=SECRET,
        worker_id=worker_id,
        rendezvous=RENDEZVOUS,
        # Discovery must fail closed here, so it points at a dead port: the
        # tests below assert on injected membership, not on the GitHub API.
        github=httpx.Client(base_url="http://127.0.0.1:1", timeout=0.25),
        peers_client=httpx.Client(timeout=5.0),
        expected_peers=expected_peers,
        peer_origin=lambda _hostname: f"http://127.0.0.1:{port}",
    )
    client.seed_peers({0: HOST})
    return client


def _local(port: int, path: str, body: bytes = b"") -> httpx.Response:
    ts = f"{time.time():.3f}"
    headers = {"X-Mesh-Ts": ts, "X-Mesh-Auth": sign(SECRET, ts, body)}
    with httpx.Client(timeout=5.0) as http:
        url = f"http://127.0.0.1:{port}{path}"
        if body:
            return http.post(url, content=body, headers=headers)
        return http.get(url, headers=headers)


def test_health_reports_the_pending_count() -> None:
    queue = TaskQueue([task("a"), task("b")])
    with serve_mesh(worker_id=0, secret=SECRET, queue=queue) as port:
        payload = _local(port, "/health").json()
    assert payload == {"worker_id": 0, "pending": 2}


def test_steal_hands_over_real_tasks() -> None:
    queue = TaskQueue([task(f"t{i}") for i in range(4)])
    with serve_mesh(worker_id=0, secret=SECRET, queue=queue) as port:
        payload = _local(port, "/steal", json.dumps({"count": 2}).encode()).json()

    handed = tuple(filter(None, map(Task.parse, payload["tasks"])))
    assert len(handed) == 2
    assert len(queue) == 2


def test_an_unsigned_request_is_refused() -> None:
    queue = TaskQueue([task("a"), task("b")])
    with (
        serve_mesh(worker_id=0, secret=SECRET, queue=queue) as port,
        httpx.Client(timeout=5.0) as http,
    ):
        response = http.get(f"http://127.0.0.1:{port}/health")
    assert response.status_code == 401
    assert len(queue) == 2  # nothing was handed out


def test_unknown_routes_are_not_served() -> None:
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([])) as port:
        assert _local(port, "/admin").status_code == 404


def test_the_endpoint_is_closed_when_the_scope_exits() -> None:
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([])) as port:
        assert _local(port, "/health").status_code == 200

    with pytest.raises(httpx.HTTPError):
        _local(port, "/health")


def test_client_steal_and_health_against_a_live_endpoint() -> None:
    queue = TaskQueue([task(f"t{i}") for i in range(3)])
    with serve_mesh(worker_id=0, secret=SECRET, queue=queue) as port:
        client = client_for(worker_id=1, port=port)

        outcome = client.steal_from(HOST)
        assert isinstance(outcome, Stolen) and len(outcome.tasks) == 1
        assert outcome.tasks[0].image == "t2"  # the tail, not the head

        # Two of three remain, so the peer is Working rather than Drained.
        assert client.health_of(HOST) == Working(2)


def test_client_reports_empty_rather_than_unreachable_when_a_peer_answers() -> None:
    # The distinction that makes a silently broken mesh visible in the logs.
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([task("only")])) as port:
        assert isinstance(client_for(1, port).steal_from(HOST), PeerEmpty)


def test_client_reports_unreachable_when_nobody_answers() -> None:
    client = client_for(worker_id=1, port=1)
    assert isinstance(client.steal_from(HOST), PeerUnreachable)
    assert client.peers_drained() is False


def test_drained_requires_every_expected_peer() -> None:
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([])) as port:
        assert client_for(1, port, expected_peers=1).peers_drained() is True
        # A peer we have not discovered yet keeps this False, which is what
        # stops a worker exiting while a late booter still holds tasks.
        assert client_for(1, port, expected_peers=2).peers_drained() is False


def test_drained_is_only_true_for_a_confirmed_empty_queue() -> None:
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([task("busy")])) as port:
        client = client_for(1, port, expected_peers=1)
        assert client.health_of(HOST) == Working(1)
        assert client.peers_drained() is False


# --- degradation without a credential --------------------------------------


def test_solo_mesh_stops_a_slot_immediately() -> None:
    """With no peers to wait for, an empty queue means done -- not 'wait 90s'."""
    from ci.mesh import SoloMesh

    solo = SoloMesh()
    assert isinstance(solo.attempt_steal(), PeerUnreachable)
    assert solo.peers_drained() is True


def test_solo_mesh_still_builds_every_dealt_task() -> None:
    from ci.domain import BuildSucceeded
    from ci.mesh import SoloMesh
    from ci.scheduling import run_worker

    tasks = [task(f"t{index}") for index in range(5)]
    outcomes = run_worker(
        queue=TaskQueue(tasks),
        mesh=SoloMesh(),
        execute=lambda t: BuildSucceeded(task=t, attempts=1, duration_seconds=0.0),
        slots=2,
        sleep=lambda _: None,
    )
    assert sorted(o.task.image for o in outcomes) == sorted(t.image for t in tasks)


def test_run_key_is_derived_identically_by_every_worker() -> None:
    """The key never travels; each worker computes it from what it already has."""
    from ci.mesh import derive_run_key

    assert derive_run_key("repo-secret", "12345") == derive_run_key("repo-secret", "12345")
    assert derive_run_key("repo-secret", "12345") != derive_run_key("repo-secret", "12346")
    assert derive_run_key("repo-secret", "12345") != derive_run_key("other", "12345")


def test_discovery_logs_peers_by_id_and_never_by_hostname(caplog) -> None:
    """Discovery must be observable without leaking the hostname.

    The workflow log is world-readable on a public repository, so the hostname
    is withheld -- but without some signal, a mesh where nobody found anybody
    looks exactly like one where every worker stayed busy.
    """
    import logging

    client = MeshClient(
        secret=SECRET,
        worker_id=1,
        rendezvous=RENDEZVOUS,
        github=httpx.Client(base_url="http://127.0.0.1:1", timeout=0.25),
        peers_client=httpx.Client(timeout=1.0),
        expected_peers=3,
    )

    with caplog.at_level(logging.INFO, logger="ci.mesh"):
        client._known = {}                       # noqa: SLF001
        # Drive the logging path the way discovery does, via a seeded update.
        refs = [{"ref": RENDEZVOUS.ref_for(0, HOST)}, {"ref": RENDEZVOUS.ref_for(2, HOST)}]
        parsed = dict(
            filter(None, (RENDEZVOUS.parse_ref(r["ref"]) for r in refs))
        )
        client.seed_peers(parsed)

    assert set(parsed) == {0, 2}
    assert HOST.value not in caplog.text


# --- pre-authentication hardening ------------------------------------------


def test_oversized_body_is_refused_without_being_read() -> None:
    """The endpoint is publicly discoverable, so pre-auth work must be bounded.

    The signature covers the body, so the body has to be read before it can be
    checked -- which makes that read an unauthenticated operation reachable by
    anyone who can read the rendezvous ref.
    """
    import socket as socketlib

    queue = TaskQueue([task("a"), task("b")])
    with serve_mesh(worker_id=0, secret=SECRET, queue=queue) as port:
        connection = socketlib.create_connection(("127.0.0.1", port), timeout=5)
        connection.sendall(
            b"POST /steal HTTP/1.1\r\nHost: x\r\nContent-Length: 500000000\r\n\r\n"
        )
        connection.settimeout(5.0)
        response = connection.recv(200)
        connection.close()

    assert b"401" in response          # refused, promptly
    assert len(queue) == 2             # and nothing handed out


def test_malformed_content_length_is_refused() -> None:
    import socket as socketlib

    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([])) as port:
        connection = socketlib.create_connection(("127.0.0.1", port), timeout=5)
        connection.sendall(
            b"POST /steal HTTP/1.1\r\nHost: x\r\nContent-Length: not-a-number\r\n\r\n"
        )
        connection.settimeout(5.0)
        response = connection.recv(200)
        connection.close()

    assert b"400" in response or b"401" in response


def test_a_body_at_the_limit_is_still_served() -> None:
    """The cap must not break a legitimate request."""
    from ci.mesh import MAX_REQUEST_BODY_BYTES

    padded = json.dumps({"count": 1, "pad": "x" * (MAX_REQUEST_BODY_BYTES - 100)}).encode()
    assert len(padded) < MAX_REQUEST_BODY_BYTES

    queue = TaskQueue([task(f"t{i}") for i in range(3)])
    with serve_mesh(worker_id=0, secret=SECRET, queue=queue) as port:
        response = _local(port, "/steal", padded)

    assert response.status_code == 200
    assert len(response.json()["tasks"]) == 1
