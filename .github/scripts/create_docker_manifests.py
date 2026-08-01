#!/usr/bin/env python3

"""Entry point: fuse the per-platform images into multi-arch manifests."""

from __future__ import annotations

import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import assert_never

from ci.docker import run_tag
from ci.domain import Platform
from ci.env import NAME_LIST, RETRIES, BuildIdentity, read, read_json
from ci.logs import configure
from ci.retry import Exhausted, Succeeded, with_retries

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
    """The architecture-independent tags that point at the fused manifest.

    Mirrors `docker.tags_for` minus the platform suffix, including the retirement
    of the `{commit}.{date}` and `{commit}.{date_time}` composites: the batch id
    is the run-unique pointer now, and the two sets have to agree about that or a
    manifest would advertise a name no per-platform build published.
    """
    stem = f"{identity.base_image}:{image}"
    return (
        stem,
        f"{stem}.latest",
        f"{stem}.{identity.date}",
        f"{stem}.{identity.date_time}",
        f"{stem}.{identity.commit_sha}",
        f"{stem}.{identity.batch}",
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

    def fuse() -> None:
        subprocess.run(command, check=True)

    match with_retries(
        operation=fuse,
        max_retries=max_retries,
        label=f"Creating manifest for '{image}'",
        log=logger,
    ):
        case Succeeded(attempts):
            logger.info("Pushed manifest for '%s'", image)
            return ManifestPushed(image=image, attempts=attempts)
        case Exhausted(attempts, error):
            return ManifestFailed(image=image, attempts=attempts, error=error)
        case other:
            assert_never(other)


def main() -> int:
    configure()

    identity = BuildIdentity.from_environment()
    max_retries = read("MAX_RETRIES", RETRIES, default=50)
    images = read_json("IMAGES", NAME_LIST)
    architectures = read_json("PLATFORMS", NAME_LIST)
    platforms = tuple(filter(None, map(Platform.parse, architectures)))

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
