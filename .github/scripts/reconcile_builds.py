#!/usr/bin/env python3

"""Entry point: verify every expected image landed, and rebuild what did not.

This is the safety net that lets the mesh stay experimental. The registry, not
the mesh bookkeeping, decides what completed: a dead runner, a dropped steal
handoff, a protocol bug, and a worker that exited early all present identically
here and are repaired identically. Only a build that its own retry budget could
not save survives to fail the run.

Scoped to exactly one architecture, because a rebuild is a real build: the
workflow runs one instance per platform on a runner of that platform, so nothing
here is ever produced under binfmt/QEMU emulation. The instances partition the
work -- disjoint images, disjoint mesh refs -- so they need no coordination.
"""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import httpx

from ci.discovery import ConflictingDockerfiles, discover
from ci.docker import build_and_push, free_disk_space, run_tag, tag_exists
from ci.domain import BuildFailed, Platform, Task, succeeded
from ci.env import (
    COUNT,
    NAME_LIST,
    RETRIES,
    TEXT,
    BuildIdentity,
    read,
    read_json,
    write_summary,
)
from ci.logs import configure
from ci.mesh import MeshClient, Rendezvous
from ci.report import provenance_section

logger = logging.getLogger("ci.reconcile")

GITHUB_API = "https://api.github.com"

# Registry inspections are network-bound and independent, so they run wide; the
# bound exists to stay well inside registry rate limits, not to save CPU.
_INSPECT_CONCURRENCY = 8


def main() -> int:
    configure()

    identity = BuildIdentity.from_environment()
    max_retries = read("MAX_RETRIES", RETRIES, default=50)
    images = read_json("IMAGES", NAME_LIST)

    platform = Platform.parse(read("DOCKER_PLATFORM", TEXT))
    if platform is None:
        logger.error("DOCKER_PLATFORM is not a supported architecture.")
        return 1

    # This instance owns its architecture's slice of the rendezvous namespace and
    # nothing else, so the per-platform cleanups below are disjoint rather than
    # racing to delete the same refs.
    rendezvous = Rendezvous(
        repository=read("GITHUB_REPOSITORY", TEXT),
        run_id=read("GITHUB_RUN_ID", TEXT),
        platform=platform,
    )

    with httpx.Client(
        base_url=GITHUB_API,
        timeout=15.0,
        headers={
            "Authorization": f"Bearer {read('GITHUB_TOKEN', TEXT)}",
            "Accept": "application/vnd.github+json",
        },
    ) as github:
        removed = MeshClient(
            secret=b"",
            worker_id=-1,
            rendezvous=rendezvous,
            github=github,
            peers_client=github,
            expected_peers=0,
        ).cleanup()
        logger.info("Cleaned up %d %s mesh ref(s).", removed, platform)

    # Re-derive the task set from the tree rather than reconstructing paths from
    # image names. It is the same checkout at the same commit, so discovery is
    # the single definition of where an image's Dockerfile and context live --
    # reconstructing them here would quietly assume every Dockerfile sits one
    # directory down under a directory named after the image, which discovery
    # itself does not require.
    try:
        expected = discover(Path.cwd(), (platform,), max_retries).tasks
    except ConflictingDockerfiles as conflict:
        logger.error("%s", conflict)
        return 1

    # The image *set* is architecture-independent, so this still cross-checks the
    # whole plan even though the tasks are one platform's.
    if {task.image for task in expected} != set(images):
        logger.error(
            "Discovery disagrees with the planned image list; refusing to guess. "
            "planned-only=%s discovered-only=%s",
            sorted(set(images) - {task.image for task in expected}),
            sorted({task.image for task in expected} - set(images)),
        )
        return 1

    logger.info("Verifying %d expected %s image(s)...", len(expected), platform)

    def landed(task: Task) -> bool:
        return tag_exists(run_tag(task.image, str(task.platform), identity))

    with ThreadPoolExecutor(max_workers=_INSPECT_CONCURRENCY) as pool:
        present = tuple(pool.map(landed, expected))

    missing = tuple(task for task, found in zip(expected, present, strict=True) if not found)

    if not missing:
        logger.info("All expected images are present. Nothing to reconcile.")
        return 0

    logger.warning(
        "%d image(s) missing after the build stage: %s",
        len(missing),
        ", ".join(f"{task.image}.{task.platform}" for task in missing),
    )

    free_disk_space()

    # The same slot count a build worker uses, for the same reason: if a whole
    # build stage failed this list is every image in the repository, and one
    # thread each would put 30+ concurrent multi-gigabyte layer writes on one
    # disk. Sharing BUILD_SLOTS keeps reconcile the same shape as the workers it
    # is standing in for, so tuning that number tunes both.
    concurrency = min(len(missing), read("BUILD_SLOTS", COUNT, default=4))
    logger.info("Rebuilding %d image(s), %d at a time.", len(missing), concurrency)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        outcomes = tuple(pool.map(partial(build_and_push, identity=identity), missing))

    write_summary(
        [
            *provenance_section(f"Reconcile ({platform}): what each rebuild consumed", outcomes),
            "",
            f"### Reconcile ({platform})",
            "",
            f"Rebuilt {len(missing)} missing image(s).",
            "",
            *(
                f"- `{outcome.task.image}.{outcome.task.platform}`: "
                f"{'ok' if succeeded(outcome) else '**failed**'}"
                for outcome in outcomes
            ),
        ]
    )

    failures = tuple(outcome for outcome in outcomes if isinstance(outcome, BuildFailed))
    if failures:
        logger.error("Reconcile could not recover %d image(s):", len(failures))
        for failure in failures:
            logger.error("  %s: %s", failure.task.image, failure.error)
        return 1

    logger.info("Reconcile rebuilt all missing images successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
