#!/usr/bin/env python3

import glob
import logging
import multiprocessing
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


def init_logger():
    """Initializes and configures a logger for each process."""
    logger = logging.getLogger(f"docker_manifest_creator_{os.getpid()}")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s][PID %(process)d] %(message)s",
        datefmt="%Y-%m-%d.%H-%M-%S",
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


def create_and_push_manifest_for_image(
    base_image: str,
    image_name_dir: str,
    base_tags: list[str],
    max_retries: int,
):
    """Creates and pushes a Docker manifest for an image with all the provided tags."""
    logger = init_logger()
    github_sha = get_env_var("GITHUB_SHA")
    date_time_str = get_env_var("DATE_TIME_STR")

    # The most specific tags for the platform-specific images
    amd64_tag = f"{base_image}:{image_name_dir}.{github_sha}.{date_time_str}.amd64"
    arm64_tag = f"{base_image}:{image_name_dir}.{github_sha}.{date_time_str}.arm64"

    # Prepare the --tag options for all base_tags
    tag_options = []
    for tag in base_tags:
        manifest_tag = f"{base_image}:{tag}"
        tag_options.extend(["--tag", manifest_tag])

    imagetools_create_cmd = (
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
        ]
        + tag_options
        + [
            amd64_tag,
            arm64_tag,
        ]
    )

    # Retry logic
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"Creating and pushing manifest for '{image_name_dir}' with tags {base_tags} using source images '{amd64_tag}' and '{arm64_tag}' (Attempt {attempt}/{max_retries})"
            )
            subprocess.run(imagetools_create_cmd, check=True)
            logger.info(
                f"Successfully created and pushed manifest for '{image_name_dir}'"
            )
            return ManifestResult(image_name=image_name_dir, success=True)
        except subprocess.CalledProcessError as e:
            logger.warning(
                f"Attempt {attempt} failed for image '{image_name_dir}': {e}"
            )
            error_msg = str(e)
        if attempt < max_retries:
            logger.info(f"Retrying manifest creation for '{image_name_dir}'...")
    logger.error(
        f"Failed to create and push manifest for '{image_name_dir}' after {max_retries} attempts"
    )
    return ManifestResult(image_name=image_name_dir, success=False, error_msg=error_msg)


def create_and_push_manifest_in_process(args):
    """Wrapper function to create and push manifest in a separate process."""
    base_image, image_name_dir, base_tags, max_retries = args
    return create_and_push_manifest_for_image(
        base_image=base_image,
        image_name_dir=image_name_dir,
        base_tags=base_tags,
        max_retries=max_retries,
    )


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

    tasks = []

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

        tasks.append((base_image, image_name_dir, base_tags, max_retries))

    # Use multiprocessing Pool to run create_and_push_manifest in parallel
    num_processes = multiprocessing.cpu_count()
    logger.info(f"Running tasks in parallel with {num_processes} processes.")

    with multiprocessing.Pool(processes=num_processes) as pool:
        results = pool.map(create_and_push_manifest_in_process, tasks)

    failed_manifests = [result for result in results if not result.success]

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
