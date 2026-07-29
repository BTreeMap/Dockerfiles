#!/usr/bin/env python3

"""Entry point: fuse the per-platform images into multi-arch manifests."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from ci.docker import backoff_seconds, run_tag
from ci.domain import Platform
from ci.env import BuildIdentity, require_int, require_json
from ci.logs import configure

logger = logging.getLogger("ci.manifests")


@dataclass(frozen=True, slots=True)
class ManifestPushed:
    image: str
    attempts: int


@dataclass(frozen=True, slots=True)
class ManifestFailed:
    image: str
    attempts: int
    error: str


ManifestOutcome = ManifestPushed | ManifestFailed


def manifest_tags(image: str, identity: BuildIdentity) -> tuple[str, ...]:
    """The architecture-independent tags that point at the fused manifest."""
    stem = f"{identity.base_image}:{image}"
    return (
        stem,
        f"{stem}.latest",
        f"{stem}.{identity.date}",
        f"{stem}.{identity.date_time}",
        f"{stem}.{identity.commit_sha}",
        f"{stem}.{identity.commit_sha}.{identity.date}",
        f"{stem}.{identity.commit_sha}.{identity.date_time}",
    )


def push_manifest(
    image: str,
    platforms: tuple[Platform, ...],
    identity: BuildIdentity,
    max_retries: int,
) -> ManifestOutcome:
    """Fuses one image's per-platform builds, retrying to the configured budget.

    Sources are the run-unique per-platform tags, so a manifest can only ever be
    assembled from images this run produced -- never from a previous day's
    leftovers still sitting under a floating tag.
    """
    command = (
        "docker",
        "buildx",
        "imagetools",
        "create",
        *(argument for tag in manifest_tags(image, identity) for argument in ("--tag", tag)),
        *(run_tag(image, str(platform), identity) for platform in platforms),
    )

    unlimited = max_retries <= 0
    budget = "∞" if unlimited else str(max_retries)
    attempt = 0
    last_error = "no attempt was made"

    while unlimited or attempt < max_retries:
        attempt += 1
        logger.info("Creating manifest for '%s' (attempt %d/%s)", image, attempt, budget)
        try:
            subprocess.run(command, check=True)
            logger.info("Pushed manifest for '%s'", image)
            return ManifestPushed(image=image, attempts=attempt)
        except (subprocess.CalledProcessError, OSError) as error:
            last_error = str(error)
            logger.warning("Attempt %d/%s failed for '%s': %s", attempt, budget, image, error)

        if not unlimited and attempt >= max_retries:
            break
        time.sleep(backoff_seconds(attempt))

    logger.error("Failed to push manifest for '%s' after %d attempt(s)", image, attempt)
    return ManifestFailed(image=image, attempts=attempt, error=last_error)


def main() -> int:
    configure()

    identity = BuildIdentity.from_environment()
    max_retries = require_int("MAX_RETRIES", 50)
    images: list[str] = require_json("IMAGES")
    platforms = tuple(filter(None, map(Platform.parse, require_json("PLATFORMS"))))

    if not images or not platforms:
        logger.error("Discovery supplied no images or no platforms.")
        return 1

    logger.info("Creating manifests for %d image(s) across %s", len(images), list(platforms))

    # imagetools work is registry-side and I/O bound, so these run concurrently.
    with ThreadPoolExecutor(max_workers=max(1, len(images))) as pool:
        outcomes = tuple(
            pool.map(lambda image: push_manifest(image, platforms, identity, max_retries), images)
        )

    failures = tuple(outcome for outcome in outcomes if isinstance(outcome, ManifestFailed))
    if failures:
        logger.error("Some manifests failed to create:")
        for failure in failures:
            logger.error("  '%s': %s", failure.image, failure.error)
        return 1

    logger.info("All manifests created and pushed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
