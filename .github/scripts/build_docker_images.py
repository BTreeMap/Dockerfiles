#!/usr/bin/env python3

import datetime
import glob
import logging
import multiprocessing
import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class BuildResult:
    image_name: str
    success: bool
    attempts: int
    error_msg: str | None = None


def init_logger():
    """Initializes and returns a logger for the Docker builder."""
    logger = logging.getLogger("docker_builder")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s] %(message)s", datefmt="%Y-%m-%d.%H-%M-%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def get_env_var(var_name, default=None):
    """Retrieves an environment variable, raising an error if not found."""
    value = os.environ.get(var_name, default)
    if value is None:
        raise ValueError(f"Environment variable '{var_name}' is not set")
    return value


def build_and_push_image(build_args) -> BuildResult:
    """Builds and pushes a Docker image, handling retries on failure."""
    (
        dockerfile_path,
        base_image,
        date_str,
        date_time_str,
        commit_hash,
        max_retries,
        logger_lock,
    ) = build_args

    # Extract directory and construct the image name based on the directory name
    directory_path = os.path.dirname(dockerfile_path)
    image_name_dir = os.path.basename(directory_path).lower()

    # Constructing image tags based on various criteria
    tags = [
        f"{base_image}:{image_name_dir}",
        f"{base_image}:{image_name_dir}.latest",
        f"{base_image}:{image_name_dir}.{date_str}",
        f"{base_image}:{image_name_dir}.{date_time_str}",
        f"{base_image}:{image_name_dir}.{commit_hash}",
        f"{base_image}:{image_name_dir}.{commit_hash}.{date_str}",
        f"{base_image}:{image_name_dir}.{commit_hash}.{date_time_str}",
    ]

    builder_name = f"builder_{image_name_dir}"

    # Prepare the build command
    buildx_command = [
        "docker",
        "buildx",
        "build",
        "--push",
        "--no-cache",
        "--builder",
        builder_name,
        "--platform",
        "linux/amd64,linux/arm64",
    ]
    for tag in tags:
        buildx_command.extend(["--tag", tag])
    buildx_command.extend(["--file", dockerfile_path, directory_path])

    # Commands to create and remove the Docker builder
    create_builder_command = ["docker", "buildx", "create", "--name", builder_name]
    remove_builder_command = ["docker", "buildx", "rm", builder_name]

    try:
        error_msg = "No Error"
        # Create builder
        subprocess.run(create_builder_command, check=True)

        # Retry logic for building the image
        for attempt in range(1, max_retries + 1):
            try:
                with logger_lock:
                    logger.info(
                        f"Building image for {tags[0]} (attempt {attempt}/{max_retries}) with tags:"
                    )
                    for tag in tags:
                        logger.info(f" - {tag}")
                subprocess.run(buildx_command, check=True)
                return BuildResult(
                    image_name=tags[0],
                    success=True,
                    attempts=attempt,
                )  # Exit if build is successful
            except BaseException as e:
                error_msg = str(e)
                with logger_lock:
                    logger.warning(
                        f"Build failed for image {tags[0]} on attempt {attempt}/{max_retries}: {e}",
                        exc_info=True,
                    )

        # Log failure after all attempts
        with logger_lock:
            logger.error(
                f"Failed to build image {tags[0]} after {max_retries} attempts."
            )
        return BuildResult(
            image_name=tags[0],
            success=False,
            attempts=max_retries,
            error_msg=error_msg,
        )

    finally:
        # Cleanup: Remove the Docker builder
        subprocess.run(remove_builder_command, check=False)


def main():
    global logger

    logger = init_logger()

    # Get environment variables
    docker_registry = get_env_var("DOCKER_REGISTRY").lower()
    docker_image_name = get_env_var("DOCKER_IMAGE_NAME").lower()
    max_retries = int(get_env_var("MAX_RETRIES", "3"))
    github_sha = get_env_var("GITHUB_SHA")

    # Get current date and time in UTC for tagging images
    current_time = datetime.datetime.now(datetime.timezone.utc)
    date_str = current_time.strftime("%Y-%m-%d")
    date_time_str = current_time.strftime("%Y-%m-%d.%H-%M-%S")

    # Form the base image path
    base_image = f"{docker_registry}/{docker_image_name}"

    logger.info(f"Starting Docker builds with base image: {base_image}")

    # Find all Dockerfiles
    dockerfiles = glob.glob("**/Dockerfile", recursive=True)
    if not dockerfiles:
        logger.error("No Dockerfiles found.")
        sys.exit(1)

    dockerfiles.sort(reverse=True)
    logger.info("Found Dockerfiles:")
    for dockerfile in dockerfiles:
        logger.info(f" - {dockerfile}")

    # Enable support for multi-platform builds
    enable_binfmt_command = [
        "docker",
        "run",
        "--privileged",
        "--rm",
        "tonistiigi/binfmt",
        "--install",
        "all",
    ]

    # Execute the command to enable binfmt
    subprocess.run(enable_binfmt_command, check=True)

    # Prepare arguments for building images
    args_list = []
    manager = multiprocessing.Manager()
    logger_lock = manager.Lock()

    for dockerfile in dockerfiles:
        args = (
            dockerfile,
            base_image,
            date_str,
            date_time_str,
            github_sha,
            max_retries,
            logger_lock,
        )
        args_list.append(args)

    # Use multiprocessing to build images in parallel
    num_processes = multiprocessing.cpu_count()
    logger.info(
        f"Starting Docker builds in parallel using up to {num_processes} processes."
    )

    with multiprocessing.Pool(processes=num_processes) as pool:
        results = pool.map(build_and_push_image, args_list)

    # Check the results for any failures
    failed_builds = list(filter(lambda result: not result.success, results))

    if failed_builds:
        logger.error("Some builds failed:")
        for failure in failed_builds:
            logger.error(
                f"Failed to build image '{failure.image_name}' after {failure.attempts} attempts. Error: {failure.error_msg}"
            )
        sys.exit(1)
    else:
        logger.info("All builds completed successfully.")


if __name__ == "__main__":
    main()
