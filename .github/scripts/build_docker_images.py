#!/usr/bin/env python3

"""Entry point: build this worker's share, stealing from peers when idle.

A thin imperative shell. It acquires the resources the mesh needs -- HTTP
clients, a verified cloudflared, a quick tunnel, a listening endpoint -- as
nested scopes, so every one of them is released on every exit path, then hands
control to the scheduler.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import ExitStack
from functools import partial

import httpx

from ci.docker import build_and_push, free_disk_space
from ci.domain import (
    BuildFailed,
    BuildOutcome,
    BuildSucceeded,
    Installed,
    InstallFailed,
    Platform,
    Task,
    TunnelReady,
    TunnelUnavailable,
)
from ci.env import (
    COUNT,
    INDEX,
    JSON_ARRAY,
    OPTIONAL_TEXT,
    TEXT,
    BuildIdentity,
    read,
    read_json,
    write_summary,
)
from ci.logs import configure
from ci.mesh import MeshClient, Rendezvous, SoloMesh, derive_run_key, serve_mesh
from ci.report import outcome_rows, provenance_section
from ci.scheduling import TaskQueue, run_worker
from ci.tunnel import quick_tunnel, resolve_binary
from ci.utilisation import effective_parallelism, intervals_of, peak_concurrency

logger = logging.getLogger("ci.worker")

GITHUB_API = "https://api.github.com"


def summarise(
    worker_id: int, outcomes: tuple[BuildOutcome, ...], dealt: frozenset[str], slots: int
) -> None:
    """Reports per-image timings, steal origin, and how busy the slots were.

    Durations are recorded not to feed a scheduler -- stealing needs no cost
    estimates -- but so the slowest image stays visible, since that is the floor
    no amount of parallelism can go below.

    Effective parallelism is the number to tune BUILD_SLOTS against. Materially
    below the slot count means slots idled waiting for work, so raising it buys
    nothing. At or near it means they were saturated and a higher count is worth
    testing -- against disk, which is what actually collides.
    """
    spans = intervals_of((o.started_at, o.duration_seconds) for o in outcomes)
    achieved = effective_parallelism(spans)
    total = sum(o.duration_seconds for o in outcomes)

    write_summary(
        [
            f"### Worker {worker_id}",
            "",
            f"- slots configured: **{slots}**",
            f"- effective parallelism: **{achieved:.2f}** "
            f"({achieved / slots * 100:.0f}% of configured)",
            f"- peak concurrent builds: **{peak_concurrency(spans)}**",
            f"- build time total {total / 60:.1f} min across {len(outcomes)} image(s)",
            "",
            "| Image | Result | Attempts | Duration | Origin |",
            "| --- | --- | --- | --- | --- |",
            *outcome_rows(outcomes, dealt),
            *provenance_section(f"Worker {worker_id}: what each image consumed", outcomes),
            "",
        ]
    )


def main() -> int:
    configure()
    free_disk_space()

    identity = BuildIdentity.from_environment()
    worker_id = read("WORKER_ID", INDEX)
    worker_count = read("WORKER_COUNT", COUNT, default=4)
    token = read("GITHUB_TOKEN", TEXT)
    run_id = read("GITHUB_RUN_ID", TEXT)
    run_attempt = read("GITHUB_RUN_ATTEMPT", TEXT, default="1")

    # The mesh credential is optional by design. Without it a worker builds
    # the share it was dealt and reconcile covers the rest, so a missing
    # secret costs stealing -- never an image.
    repository_secret = read("MESH_SECRET", OPTIONAL_TEXT, default="")

    platform = Platform.parse(read("DOCKER_PLATFORM", TEXT))
    if platform is None:
        logger.error("DOCKER_PLATFORM is not a supported architecture.")
        return 1

    raw_tasks = read_json("WORKER_TASKS", JSON_ARRAY)
    tasks = tuple(filter(None, map(Task.parse, raw_tasks)))
    if len(tasks) != len(raw_tasks):
        logger.error("Rejected %d malformed task(s) in WORKER_TASKS.", len(raw_tasks) - len(tasks))
        return 1

    dealt = frozenset(task.image for task in tasks)
    logger.info(
        "Worker %d (%s) dealt %d task(s): %s",
        worker_id,
        platform,
        len(tasks),
        ", ".join(sorted(dealt)) or "(none)",
    )

    queue = TaskQueue(tasks)
    rendezvous = Rendezvous(
        repository=read("GITHUB_REPOSITORY", TEXT), run_id=run_id, platform=platform
    )

    # Overridable because the right value is empirical: these builds are
    # dominated by network fetches and layer I/O, so the core count is only a
    # starting point. Tune against the effective-parallelism figure in the job
    # summary, and against disk -- several of these images write multi-gigabyte
    # layers, and disk is what actually collides on a runner.
    #
    # COUNT rather than a plain integer because zero slots starts zero threads,
    # records zero outcomes, and exits 0 -- a green run that built nothing.
    detected = max(1, os.cpu_count() or 1)
    slots = read("BUILD_SLOTS", COUNT, default=detected)
    logger.info("Using %d build slot(s) (runner reports %d CPU(s))", slots, detected)

    # The run's identity is fixed before any task is dealt, so it is bound once
    # here rather than threaded through the scheduler: `run_worker` needs a
    # `Task -> BuildOutcome`, and partial application is what turns the
    # two-argument builder into exactly that without inventing a wrapper.
    build = partial(build_and_push, identity=identity)

    if not repository_secret:
        logger.warning(
            "MESH_SECRET is not configured; work stealing is disabled and this "
            "worker will build only the %d task(s) it was dealt.",
            len(tasks),
        )
        outcomes = run_worker(queue=queue, mesh=SoloMesh(), execute=build, slots=slots)
    else:
        with ExitStack() as scope:
            github = scope.enter_context(
                httpx.Client(
                    base_url=GITHUB_API,
                    timeout=15.0,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
            )
            peers = scope.enter_context(httpx.Client(timeout=15.0))

            client = MeshClient(
                secret=derive_run_key(repository_secret, run_id, run_attempt),
                worker_id=worker_id,
                rendezvous=rendezvous,
                github=github,
                peers_client=peers,
                expected_peers=worker_count - 1,
            )

            port = scope.enter_context(serve_mesh(worker_id, client.secret, queue))

            # Joining the mesh is best-effort throughout. Every failure below
            # leaves the worker building the share it was dealt, which is the
            # whole point of dealing disjointly in the first place.
            match resolve_binary(platform):
                case Installed(path, version):
                    logger.info("Using cloudflared %s", version)
                    match scope.enter_context(quick_tunnel(path, port)):
                        case TunnelReady(hostname):
                            client.publish(hostname, identity.commit_sha)
                        case TunnelUnavailable(reason):
                            logger.warning("No tunnel (%s); building solo", reason)
                case InstallFailed(reason):
                    logger.warning("cloudflared unavailable (%s); building solo", reason)

            # cpu_count() concurrent builds: these builds are dominated by
            # network fetches and layer I/O rather than compute, so running one
            # per core raises throughput well past a single build.
            outcomes = run_worker(queue=queue, mesh=client, execute=build, slots=slots)

    summarise(worker_id, outcomes, dealt, slots)

    failures = tuple(outcome for outcome in outcomes if isinstance(outcome, BuildFailed))
    successes = tuple(outcome for outcome in outcomes if isinstance(outcome, BuildSucceeded))
    logger.info(
        "Worker %d finished: %d succeeded, %d failed.", worker_id, len(successes), len(failures)
    )

    for failure in failures:
        logger.error(
            "Image '%s' failed after %d attempts. Last error: %s",
            failure.task.image,
            failure.attempts,
            failure.error,
        )
        for name, sample in failure.metrics.items():
            logger.error("%s at failure time:\n%s", name, sample)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
