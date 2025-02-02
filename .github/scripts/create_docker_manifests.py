#!/usr/bin/env python3

import glob
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class ManifestResult:
    """Tracks the outcome of a Docker manifest creation attempt."""

    image_name: str
    success: bool
    error_msg: str | None = None


@dataclass
class DigestResult:
    """Tracks the outcome of retrieving a platform-specific digest."""

    image_tag: str
    platform: str
    success: bool
    digest: str | None = None
    error_msg: str | None = None


def init_logger():
    """Initializes and configures a logger."""
    logger = logging.getLogger("docker_manifest_creator")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s] %(message)s", datefmt="%Y-%m-%d.%H-%M-%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def get_env_var(var_name, default=None):
    """Retrieves an environment variable, returning default if provided, else raises ValueError."""
    value = os.environ.get(var_name, default)
    if value is None:
        raise ValueError(f"Environment variable '{var_name}' is not set")
    return value


import json  # Ensure this is imported at the top of your script


def get_platform_digest(image_tag, platform, logger):
    inspect_cmd = ["docker", "manifest", "inspect", image_tag]
    try:
        output = subprocess.check_output(inspect_cmd, text=True)
        manifest = json.loads(output)
        for m in manifest.get("manifests", []):
            if m.get("platform", {}).get("architecture") == platform:
                digest = m.get("digest")
                return DigestResult(
                    image_tag=image_tag, platform=platform, success=True, digest=digest
                )
        error_msg = f"No digest found for platform '{platform}' in image '{image_tag}'"
        logger.error(error_msg)
        return DigestResult(
            image_tag=image_tag, platform=platform, success=False, error_msg=error_msg
        )
    except Exception as e:
        error_msg = f"Failed to get digest for image '{image_tag}': {e}"
        logger.error(error_msg)
        return DigestResult(
            image_tag=image_tag, platform=platform, success=False, error_msg=error_msg
        )


def create_and_push_manifest(
    base_image: str,
    base_tag: str,
    logger: logging.Logger,
    max_retries: int,
):
    """Creates and pushes a Docker manifest for a single tag."""
    amd64_tag = f"{base_image}:{base_tag}.amd64"
    arm64_tag = f"{base_image}:{base_tag}.arm64"
    manifest_tag = f"{base_image}:{base_tag}"

    # Retrieve the digest for each architecture-specific image
    amd64_digest_result = get_platform_digest(amd64_tag, "amd64", logger)
    arm64_digest_result = get_platform_digest(arm64_tag, "arm64", logger)

    # Check if digest retrieval was successful for both architectures
    if not amd64_digest_result.success or not arm64_digest_result.success:
        error_msgs = []
        if amd64_digest_result.error_msg:
            error_msgs.append(amd64_digest_result.error_msg)
        if arm64_digest_result.error_msg:
            error_msgs.append(arm64_digest_result.error_msg)
        combined_error_msg = "; ".join(error_msgs)
        logger.error(
            f"Failed to get digests for manifest '{manifest_tag}': {combined_error_msg}"
        )
        return ManifestResult(
            image_name=manifest_tag, success=False, error_msg=combined_error_msg
        )

    amd64_digest = amd64_digest_result.digest
    arm64_digest = arm64_digest_result.digest

    manifest_create_cmd = [
        "docker",
        "manifest",
        "create",
        manifest_tag,
        "--amend",
        f"{amd64_tag}@{amd64_digest}",
        "--amend",
        f"{arm64_tag}@{arm64_digest}",
    ]
    manifest_push_cmd = ["docker", "manifest", "push", manifest_tag]

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"Creating manifest '{manifest_tag}' using digests '{amd64_digest}' and '{arm64_digest}' (Attempt {attempt}/{max_retries})"
            )
            subprocess.run(manifest_create_cmd, check=True)
            subprocess.run(manifest_push_cmd, check=True)
            logger.info(f"Successfully pushed manifest '{manifest_tag}'")
            return ManifestResult(image_name=manifest_tag, success=True)
        except subprocess.CalledProcessError as e:
            logger.warning(
                f"Attempt {attempt} failed for manifest '{manifest_tag}': {e}"
            )
            error_msg = str(e)
    logger.error(
        f"Failed to create and push manifest '{manifest_tag}' after {max_retries} attempts"
    )
    return ManifestResult(image_name=manifest_tag, success=False, error_msg=error_msg)


def main():
    logger = init_logger()

    # Retrieve necessary environment variables
    docker_registry = get_env_var("DOCKER_REGISTRY").lower()
    docker_image_name = get_env_var("DOCKER_IMAGE_NAME").lower()
    max_retries = int(get_env_var("MAX_RETRIES", "3"))
    github_sha = get_env_var("GITHUB_SHA")
    date_str = get_env_var("DATE_STR")
    date_time_str = get_env_var("DATE_TIME_STR")

    base_image = f"{docker_registry}/{docker_image_name}"
    logger.info(f"Base image: {base_image}")

    # Locate Dockerfiles
    dockerfiles = glob.glob("**/Dockerfile", recursive=True)
    if not dockerfiles:
        logger.error("No Dockerfiles found.")
        sys.exit(1)

    dockerfiles.sort(reverse=True)
    logger.info(f"Found {len(dockerfiles)} Dockerfiles:")

    failed_manifests = []

    for dockerfile in dockerfiles:
        dir_path = os.path.dirname(dockerfile)
        image_name_dir = os.path.basename(dir_path).lower()
        logger.info(f"Processing image: {image_name_dir}")

        base_tags = [
            f"{image_name_dir}",
            f"{image_name_dir}.latest",
            f"{image_name_dir}.{date_str}",
            f"{image_name_dir}.{date_time_str}",
            f"{image_name_dir}.{github_sha}",
            f"{image_name_dir}.{github_sha}.{date_str}",
            f"{image_name_dir}.{github_sha}.{date_time_str}",
        ]

        for base_tag in base_tags:
            result = create_and_push_manifest(
                base_image=base_image,
                base_tag=base_tag,
                logger=logger,
                max_retries=max_retries,
            )

            if not result.success:
                failed_manifests.append(result)

    if failed_manifests:
        logger.error("Some manifests failed to create:")
        for manifest in failed_manifests:
            logger.error(
                f"Manifest '{manifest.image_name}' failed: {manifest.error_msg}"
            )
        sys.exit(1)
    else:
        logger.info("All manifests created and pushed successfully.")


if __name__ == "__main__":
    main()
