#!/usr/bin/env python3

"""Entry point: verify every expected image landed, and rebuild what did not.

This is the safety net that lets the mesh stay experimental. The registry, not
the mesh bookkeeping, decides what completed: a dead runner, a dropped steal
handoff, a protocol bug, and a worker that exited early all present identically
here and are repaired identically. Only a build that its own retry budget could
not save survives to fail the run.
"""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import product

import httpx

from ci.docker import build_and_push, free_disk_space, run_tag, tag_exists
from ci.domain import BuildFailed, Platform, Task, succeeded
from ci.env import BuildIdentity, require, require_int, require_json, write_summary
from ci.logs import configure
from ci.mesh import MeshClient, Rendezvous

logger = logging.getLogger("ci.reconcile")

GITHUB_API = "https://api.github.com"

# Registry inspections are network-bound and independent, so they run wide; the
# bound exists to stay well inside registry rate limits, not to save CPU.
_INSPECT_CONCURRENCY = 8


def main() -> int:
    configure()

    identity = BuildIdentity.from_environment()
    max_retries = require_int("MAX_RETRIES", 50)
    images: list[str] = require_json("IMAGES")
    platforms = tuple(filter(None, map(Platform.parse, require_json("PLATFORMS"))))

    # Any platform works here: cleanup matches on the run, not the architecture.
    rendezvous = Rendezvous(
        repository=require("GITHUB_REPOSITORY"),
        run_id=require("GITHUB_RUN_ID"),
        platform=platforms[0],
    )

    with httpx.Client(
        base_url=GITHUB_API,
        timeout=15.0,
        headers={
            "Authorization": f"Bearer {require('GITHUB_TOKEN')}",
            "Accept": "application/vnd.github+json",
        },
    ) as github:
        removed = MeshClient(
            secret="",
            worker_id=-1,
            rendezvous=rendezvous,
            github=github,
            peers_client=github,
            expected_peers=0,
        ).cleanup()
        logger.info("Cleaned up %d mesh ref(s).", removed)

    expected = tuple(product(images, platforms))
    logger.info("Verifying %d expected platform image(s)...", len(expected))

    with ThreadPoolExecutor(max_workers=_INSPECT_CONCURRENCY) as pool:
        present = tuple(
            pool.map(lambda pair: tag_exists(run_tag(pair[0], str(pair[1]), identity)), expected)
        )

    missing = tuple(pair for pair, found in zip(expected, present, strict=True) if not found)

    if not missing:
        logger.info("All expected images are present. Nothing to reconcile.")
        return 0

    logger.warning(
        "%d image(s) missing after the build stage: %s",
        len(missing),
        ", ".join(f"{image}.{platform}" for image, platform in missing),
    )

    free_disk_space()

    rebuilt = tuple(
        Task(
            image=image,
            dockerfile=f"{image}/Dockerfile",
            context=image,
            platform=platform,
            max_retries=max_retries,
        )
        for image, platform in missing
    )

    with ThreadPoolExecutor(max_workers=max(1, len(rebuilt))) as pool:
        outcomes = tuple(pool.map(lambda task: build_and_push(task, identity), rebuilt))

    write_summary(
        [
            "",
            "### Reconcile",
            "",
            f"Rebuilt {len(missing)} missing image(s).",
            "",
            *(
                f"- `{outcome.task.image}.{outcome.task.platform}` — "
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
