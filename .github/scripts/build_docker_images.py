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

import httpx

from ci.docker import build_and_push, free_disk_space
from ci.domain import (
    BuildFailed,
    BuildOutcome,
    BuildSucceeded,
    Platform,
    Task,
    TunnelReady,
    TunnelUnavailable,
    succeeded,
)
from ci.env import BuildIdentity, optional, require, require_int, require_json, write_summary
from ci.logs import configure
from ci.mesh import MeshClient, Rendezvous, SoloMesh, derive_run_key, serve_mesh
from ci.scheduling import TaskQueue, run_worker
from ci.tunnel import Installed, InstallFailed, quick_tunnel, resolve_binary

logger = logging.getLogger("ci.worker")

GITHUB_API = "https://api.github.com"


def summarise(worker_id: int, outcomes: tuple[BuildOutcome, ...], dealt: frozenset[str]) -> None:
    """Reports per-image timings and steal origin.

    Durations are recorded not to feed a scheduler -- stealing needs no cost
    estimates -- but so the slowest image stays visible, since that is the floor
    no amount of parallelism can go below.
    """
    rows = sorted(outcomes, key=lambda outcome: -outcome.duration_seconds)
    write_summary(
        [
            f"### Worker {worker_id}",
            "",
            "| Image | Result | Attempts | Duration | Origin |",
            "| --- | --- | --- | --- | --- |",
            *(
                f"| `{outcome.task.image}.{outcome.task.platform}` "
                f"| {'ok' if succeeded(outcome) else '**failed**'} "
                f"| {outcome.attempts} "
                f"| {outcome.duration_seconds / 60:.1f} min "
                f"| {'dealt' if outcome.task.image in dealt else 'stolen'} |"
                for outcome in rows
            ),
            "",
        ]
    )


def main() -> int:
    configure()
    free_disk_space()

    identity = BuildIdentity.from_environment()
    worker_id = require_int("WORKER_ID")
    worker_count = require_int("WORKER_COUNT", 4)
    token = require("GITHUB_TOKEN")
    run_id = require("GITHUB_RUN_ID")
    run_attempt = optional("GITHUB_RUN_ATTEMPT", "1")

    # The mesh credential is optional by design. Without it a worker builds
    # the share it was dealt and reconcile covers the rest, so a missing
    # secret costs stealing -- never an image.
    repository_secret = optional("MESH_SECRET", "")

    platform = Platform.parse(require("DOCKER_PLATFORM"))
    if platform is None:
        logger.error("DOCKER_PLATFORM is not a supported architecture.")
        return 1

    raw_tasks = require_json("WORKER_TASKS")
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
        repository=require("GITHUB_REPOSITORY"), run_id=run_id, platform=platform
    )

    slots = max(1, os.cpu_count() or 1)

    def build(task: Task) -> BuildOutcome:
        return build_and_push(task, identity)

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

    summarise(worker_id, outcomes, dealt)

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
