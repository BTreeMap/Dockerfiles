"""Wire protocol, rendezvous parsing, and a live end-to-end steal."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from ci.domain import (
    Authenticated,
    Drained,
    HeaderAuthOutcome,
    HeadersAuthentic,
    HealthUnknown,
    Hostname,
    PeerEmpty,
    PeerUnreachable,
    Platform,
    Rejected,
    Stolen,
    Task,
    Working,
)
from ci.mesh import (
    MeshClient,
    Rendezvous,
    body_digest,
    derive_run_key,
    serve_mesh,
    sign_request,
    verify_body,
    verify_headers,
)
from ci.scheduling import TaskQueue

SECRET = derive_run_key("s3cret", "run-42")
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


def signed(
    method: str, path: str, body: bytes, key: bytes = SECRET, ts: str = "1000.0"
) -> HeaderAuthOutcome:
    digest = body_digest(body)
    return verify_headers(
        key=key, method=method, path=path, timestamp=ts,
        declared_length=str(len(body)), digest=digest,
        presented=sign_request(key, method, path, ts, len(body), digest),
        now=1000.0,
    )


def test_a_correct_signature_authenticates() -> None:
    outcome = signed("POST", "/steal", b'{"count": 1}')
    assert isinstance(outcome, HeadersAuthentic)
    assert outcome.content_length == 12


def test_a_wrong_key_is_rejected() -> None:
    body, ts = b"{}", "1000.0"
    digest = body_digest(body)
    outcome = verify_headers(
        key=SECRET, method="POST", path="/steal", timestamp=ts,
        declared_length="2", digest=digest,
        presented=sign_request(derive_run_key("other", "run-42"), "POST", "/steal", ts, 2, digest),
        now=1000.0,
    )
    assert isinstance(outcome, Rejected) and "signature" in outcome.reason


def test_a_health_credential_cannot_be_replayed_as_a_steal() -> None:
    """Regression: the signature must bind the method and path.

    Previously it covered only the timestamp and body, and both endpoints carry
    an empty body -- so a captured read-only /health credential verified
    unchanged against the destructive /steal endpoint and drained the queue.
    """
    ts = "1000.0"
    digest = body_digest(b"")
    health_mac = sign_request(SECRET, "GET", "/health", ts, 0, digest)

    lifted = verify_headers(
        key=SECRET, method="POST", path="/steal", timestamp=ts,
        declared_length="0", digest=digest, presented=health_mac, now=1000.0,
    )
    assert isinstance(lifted, Rejected) and "signature" in lifted.reason


def test_a_stale_request_is_rejected() -> None:
    body, ts = b"", "1000.0"
    digest = body_digest(body)
    outcome = verify_headers(
        key=SECRET, method="GET", path="/health", timestamp=ts,
        declared_length="0", digest=digest,
        presented=sign_request(SECRET, "GET", "/health", ts, 0, digest),
        now=2000.0,
    )
    assert isinstance(outcome, Rejected) and "skew" in outcome.reason


def test_a_malformed_timestamp_is_rejected_rather_than_raising() -> None:
    outcome = verify_headers(
        key=SECRET, method="GET", path="/health", timestamp="not-a-number",
        declared_length="0", digest=body_digest(b""), presented="x", now=1000.0,
    )
    assert isinstance(outcome, Rejected) and "malformed timestamp" in outcome.reason


def test_an_oversized_declared_length_is_rejected_before_any_read() -> None:
    outcome = verify_headers(
        key=SECRET, method="POST", path="/steal", timestamp="1000.0",
        declared_length="5000000000", digest=body_digest(b""), presented="x", now=1000.0,
    )
    assert isinstance(outcome, Rejected) and "exceeds" in outcome.reason


def test_a_body_that_does_not_match_its_signed_digest_is_rejected() -> None:
    """The digest is what lets the signature cover a body it has not read."""
    authentic = signed("POST", "/steal", b'{"count": 1}')
    assert isinstance(authentic, HeadersAuthentic)
    assert isinstance(verify_body(b'{"count": 9}', authentic), Rejected)
    assert isinstance(verify_body(b'{"count": 1}', authentic), Authenticated)


def test_an_oversized_repository_secret_is_accepted() -> None:
    """BLAKE2b raises on keys over 64 bytes, unlike HMAC which pre-hashes."""
    assert len(derive_run_key("x" * 5000, "run-1")) == 32


def test_a_re_run_does_not_reuse_the_previous_attempts_key() -> None:
    """GITHUB_RUN_ID is stable across re-runs; only the attempt increments."""
    first = derive_run_key("secret", "999", "1")
    second = derive_run_key("secret", "999", "2")
    assert first != second


def test_key_derivation_is_scoped_apart_from_request_signing() -> None:
    """Personalisation keeps one key's two uses from colliding."""
    key = derive_run_key("secret", "run-1")
    assert key != derive_run_key("secret", "run-2")
    assert sign_request(key, "GET", "/health", "1.0", 0, body_digest(b"")) != key.hex()


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
    method = "POST" if body else "GET"
    ts = f"{time.time():.3f}"
    digest = body_digest(body)
    headers = {
        "X-Mesh-Ts": ts,
        "X-Mesh-Body": digest,
        "X-Mesh-Auth": sign_request(SECRET, method, path, ts, len(body), digest),
    }
    with httpx.Client(timeout=5.0) as http:
        url = f"http://127.0.0.1:{port}{path}"
        if body:
            return http.post(url, content=body, headers=headers)
        return http.get(url, headers=headers)


def test_health_reports_what_the_peer_would_hand_over() -> None:
    """Two queued, one retained, so one is on offer.

    The published figure is the releasable count rather than the queue depth,
    because those two disagree at exactly the size where the disagreement costs
    a thief the whole grace period: a peer holding one task refuses every steal
    while a depth report calls it busy.
    """
    queue = TaskQueue([task("a"), task("b")])
    with serve_mesh(worker_id=0, secret=SECRET, queue=queue) as port:
        payload = _local(port, "/health").json()
    assert payload == {"worker_id": 0, "spare": 1}


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

        # Two of three remain, one of which is retained, so one is still spare.
        assert client.health_of(HOST) == Working(1)


def test_client_reports_empty_rather_than_unreachable_when_a_peer_answers() -> None:
    # The distinction that makes a silently broken mesh visible in the logs.
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([task("only")])) as port:
        assert isinstance(client_for(1, port).steal_from(HOST), PeerEmpty)


def test_client_reports_unreachable_when_nobody_answers() -> None:
    """The distinction that makes a silently broken mesh visible in the logs."""
    assert isinstance(client_for(worker_id=1, port=1).steal_from(HOST), PeerUnreachable)


def test_a_peer_that_has_not_published_keeps_a_worker_waiting() -> None:
    """The one case the grace period exists for, and the only thing bounding it.

    A worker that has not published its rendezvous ref may still be booting, and
    nothing this client can observe distinguishes that from a worker that will
    never arrive. So it waits, and the grace period is what stops it waiting for
    the rest of the run.
    """
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([])) as port:
        assert client_for(1, port, expected_peers=1).peers_drained() is True
        # A second peer, expected but never seen: the count is short and this
        # stays False whatever the peers that did publish have to say.
        assert client_for(1, port, expected_peers=2).peers_drained() is False


def test_a_peer_holding_only_its_retained_task_is_drained() -> None:
    """The case that used to cost a run the full grace period.

    A victim never hands over its last task, so this peer has already given its
    final answer to every steal that will ever be attempted against it. Read as
    "busy", it kept a thief polling for ninety seconds to be told the same thing
    thirty times.
    """
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([task("mine")])) as port:
        client = client_for(1, port, expected_peers=1)
        assert client.health_of(HOST) == Drained()
        assert client.peers_drained() is True


def test_a_peer_with_work_to_give_keeps_a_thief_alive() -> None:
    with serve_mesh(worker_id=0, secret=SECRET, queue=TaskQueue([task("mine"), task("spare")])) as (
        port
    ):
        client = client_for(1, port, expected_peers=1)
        assert client.health_of(HOST) == Working(1)
        assert client.peers_drained() is False


def test_a_peer_that_published_and_cannot_be_reached_is_finished() -> None:
    """The case that cost the last worker standing the whole grace period.

    Its ref exists, so that worker booted and served; unreachable now means it
    has exited, which it does only with an empty queue, or that its tunnel died,
    which nothing re-establishes. Neither will hand over a task.

    This is the evidence the ref carries and prior contact does not. Contact only
    happens when a slot goes idle, and the last worker to finish -- the one that
    stayed busy longest, by definition -- has spoken to nobody by the time every
    peer is already gone.
    """
    client = client_for(worker_id=1, port=1, expected_peers=1)  # nothing listens on port 1
    assert isinstance(client.health_of(HOST), HealthUnknown)
    assert client.peers_drained() is True


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


def test_discovery_logs_peers_by_id_and_never_by_hostname(
    caplog: pytest.LogCaptureFixture,
) -> None:
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
            b"POST /steal HTTP/1.1\r\nHost: x\r\nContent-Length: 5000000000\r\n\r\n"
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


def test_an_unauthenticated_flood_cannot_exhaust_capacity() -> None:
    """No cap exists to exhaust, and unsigned requests never reach the body read.

    An earlier version bounded concurrent requests before authenticating, which
    let anyone who could reach the endpoint occupy every slot and silence the
    mesh without holding the key at all.
    """
    import ci.mesh as mesh_module
    from ci.mesh import MAX_REQUEST_BODY_BYTES

    assert not hasattr(mesh_module, "MAX_CONCURRENT_REQUESTS")
    assert MAX_REQUEST_BODY_BYTES == 8 * 1024 * 1024

    queue = TaskQueue([task(f"t{i}") for i in range(6)])
    with serve_mesh(worker_id=0, secret=SECRET, queue=queue) as port:
        # Many unsigned requests in a row: each is refused, none is served, and
        # a legitimate caller still gets through afterwards.
        with httpx.Client(timeout=5.0) as http:
            for _ in range(40):
                assert http.get(f"http://127.0.0.1:{port}/health").status_code == 401
        assert _local(port, "/health").status_code == 200

    assert len(queue) == 6


# --- cleanup ---------------------------------------------------------------


def test_cleanup_deletes_only_this_platform_s_refs() -> None:
    """Reconcile runs once per architecture, so its sweeps must be disjoint.

    A run-wide sweep from each instance would have both racing to delete the same
    refs, and every loser would log a deletion failure that means nothing. Scoped
    to the rendezvous prefix the sweeps partition the namespace instead, and their
    union over the platforms still covers the run.
    """
    listed: list[str] = []
    deleted: list[str] = []

    refs = {
        "refs/mesh/42/amd64/0/busy-blue-cat.trycloudflare.com",
        "refs/mesh/42/amd64/1/calm-red-fox.trycloudflare.com",
        "refs/mesh/42/arm64/0/lone-grey-owl.trycloudflare.com",
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            prefix = request.url.path.split("/git/matching-refs/", 1)[1]
            listed.append(prefix)
            matching = sorted(ref for ref in refs if ref.startswith(f"refs/{prefix}/"))
            return httpx.Response(200, json=[{"ref": ref} for ref in matching])
        deleted.append(request.url.path.split("/git/", 1)[1])
        return httpx.Response(204)

    def sweep(platform: Platform) -> int:
        github = httpx.Client(
            base_url="https://api.github.invalid",
            transport=httpx.MockTransport(handle),
        )
        return MeshClient(
            secret=b"",
            worker_id=-1,
            rendezvous=Rendezvous(repository="owner/repo", run_id="42", platform=platform),
            github=github,
            peers_client=github,
            expected_peers=0,
        ).cleanup()

    assert sweep(Platform.AMD64) == 2
    assert listed == ["mesh/42/amd64"]
    assert deleted == sorted(ref for ref in refs if "/amd64/" in ref)

    # The other architecture's sweep is disjoint, and the two together are total.
    assert sweep(Platform.ARM64) == 1
    assert set(deleted) == refs
