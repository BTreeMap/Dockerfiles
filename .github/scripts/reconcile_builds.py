#!/usr/bin/env python3

"""
Verifies that every expected image actually landed, and rebuilds what did not.

This is the safety net that lets the mesh stay experimental. The registry, not
the mesh bookkeeping, is the source of truth for completion: whatever caused an
image to be missing -- a dead runner, a dropped steal handoff, a protocol bug,
a worker that exited early -- shows up the same way here and is fixed the same
way. A build that a worker's retry budget could not save is the only thing that
survives to fail the run.
"""


import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import build_docker_images as builder
from mesh import Task

logger = logging.getLogger("reconcile")


def get_env_var(var_name: str, default: str | None = None) -> str:
    value = os.environ.get(var_name, default)
    if value is None:
        raise ValueError(f"Environment variable '{var_name}' is not set")
    return value


def tag_exists(tag: str) -> bool:
    """Checks the registry for a tag, treating any error as 'not present'.

    Erring towards a rebuild is the safe direction: rebuilding something that
    exists wastes minutes and republishes identical content, while wrongly
    assuming presence would leave a hole.
    """
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", tag],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def cleanup_mesh_refs(repository: str, run_id: str, token: str) -> None:
    """Deletes this run's rendezvous refs so they do not accumulate."""

    def request(method: str, path: str):
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read()
            return json.loads(raw) if raw else None

    try:
        refs = request("GET", f"/repos/{repository}/git/matching-refs/mesh/{run_id}") or []
    except Exception as error:
        logger.warning("Could not list mesh refs for cleanup: %s", error)
        return

    for entry in refs:
        ref = entry.get("ref", "")
        if not ref.startswith("refs/"):
            continue
        try:
            request("DELETE", f"/repos/{repository}/git/refs/{ref[len('refs/'):]}")
        except Exception as error:
            logger.warning("Could not delete %s: %s", ref, error)

    logger.info("Cleaned up %d mesh ref(s).", len(refs))


def main() -> None:
    builder.logger = builder.init_logger()
    logger.addHandler(builder.logger.handlers[0])
    logger.setLevel(logging.INFO)
    logger.propagate = False

    docker_registry = get_env_var("DOCKER_REGISTRY").lower()
    docker_image_name = get_env_var("DOCKER_IMAGE_NAME").lower()
    github_sha = get_env_var("GITHUB_SHA")
    date_str = get_env_var("DATE_STR")
    date_time_str = get_env_var("DATE_TIME_STR")
    max_retries = int(get_env_var("MAX_RETRIES", "50"))
    repository = get_env_var("GITHUB_REPOSITORY")
    run_id = get_env_var("GITHUB_RUN_ID")
    token = get_env_var("GITHUB_TOKEN")

    base_image = f"{docker_registry}/{docker_image_name}"
    images = json.loads(get_env_var("IMAGES"))
    platforms = json.loads(get_env_var("PLATFORMS"))

    cleanup_mesh_refs(repository, run_id, token)

    # The run-unique tag is the one that proves *this* run produced the image;
    # the floating tags would still resolve to a previous day's build.
    expected: list[tuple[str, str, str]] = [
        (image, platform, f"{base_image}:{image}.{github_sha}.{date_time_str}.{platform}")
        for image in images
        for platform in platforms
    ]

    logger.info("Verifying %d expected platform image(s)...", len(expected))

    with ThreadPoolExecutor(max_workers=8) as pool:
        presence = list(pool.map(lambda entry: tag_exists(entry[2]), expected))

    missing = [entry for entry, present in zip(expected, presence) if not present]

    if not missing:
        logger.info("All expected images are present. Nothing to reconcile.")
        return

    logger.warning(
        "%d image(s) missing after the build stage: %s",
        len(missing),
        ", ".join(f"{image}.{platform}" for image, platform, _ in missing),
    )

    builder.free_disk_space()

    def rebuild(entry: tuple[str, str, str]) -> builder.BuildResult:
        image, platform, _ = entry
        return builder.build_and_push_image(
            task=Task(
                image=image,
                dockerfile=f"{image}/Dockerfile",
                context=image,
                platform=platform,
                max_retries=max_retries,
            ),
            base_image=base_image,
            date_str=date_str,
            date_time_str=date_time_str,
            commit_hash=github_sha,
        )

    with ThreadPoolExecutor(max_workers=max(1, os.cpu_count() or 1)) as pool:
        results = list(pool.map(rebuild, missing))

    failures = [result for result in results if not result.success]

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as handle:
            handle.write(f"\n### Reconcile\n\nRebuilt {len(missing)} missing image(s).\n\n")
            for result in results:
                state = "ok" if result.success else "**failed**"
                handle.write(f"- `{result.image_name.split(':')[-1]}` — {state}\n")

    if failures:
        logger.error("Reconcile could not recover %d image(s):", len(failures))
        for failure in failures:
            logger.error("  %s: %s", failure.image_name, failure.error_msg)
        sys.exit(1)

    logger.info("Reconcile rebuilt all missing images successfully.")


if __name__ == "__main__":
    main()
