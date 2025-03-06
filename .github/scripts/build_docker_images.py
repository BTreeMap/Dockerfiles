#!/usr/bin/env python3

import glob
import logging
import multiprocessing
import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class BuildResult:
    """Tracks the outcome of a Docker image build attempt."""

    image_name: str
    success: bool
    attempts: int
    error_msg: str | None = None
    system_metrics: dict | None = None


def remove_packages(pkg_patterns: list[str]) -> None:
    """
    Removes packages matching the given patterns using dpkg and apt.

    Args:
        pkg_patterns: List of package name patterns, supporting globbing.
    """
    try:
        all_packages = []
        for pattern in pkg_patterns:
            # Call dpkg directly and process output in Python
            result = subprocess.run(
                ["dpkg", "--get-selections", pattern],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                # Parse the output to extract package names (first column)
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        package_name = line.split()[0]
                        all_packages.append(package_name)

        # Remove duplicates and sort
        packages_to_remove = sorted(set(all_packages))

        if packages_to_remove:
            logger.info(
                "Found {} packages to remove: \n\n - {}\n\n".format(
                    len(packages_to_remove),
                    "\n - ".join(packages_to_remove),
                )
            )
            # Remove packages that actually exist
            subprocess.run(
                ["sudo", "apt-get", "remove", "--purge", "-y"] + packages_to_remove,
                check=False,
            )
            subprocess.run(
                ["sudo", "dpkg", "--purge"] + packages_to_remove, check=False
            )
        else:
            logger.warning("No matching packages found for removal")
    except Exception as e:
        logger.error(f"Failed to get package list: {e}, continuing with cleanup")


def free_disk_space():
    """
    Removes unnecessary packages and directories to free up disk space for Docker builds.

    This function executes a series of cleanup operations targeting commonly unused
    packages in CI environments, helping prevent "no space left on device" errors.
    """
    logger.info("Current disk space before cleanup:")
    subprocess.run(["df", "-h"], check=False)

    # Group apt-get removal commands together with packages sorted alphabetically
    logger.info("Removing unnecessary packages...")
    # List packages to remove with globbing patterns
    pkg_patterns = [
        "dotnet-*",
        "golang-*",
        "llvm-*",
        "temurin-*-jdk",
        "azure-cli",
        "firefox",
        "snapd",
    ]
    remove_packages(pkg_patterns)

    # Clean up package management system
    logger.info("Performing system cleanup...")
    subprocess.run(["sudo", "apt-get", "autoremove", "-y"], check=False)
    subprocess.run(["sudo", "apt-get", "clean"], check=False)

    # Group directory removals together with paths sorted alphabetically
    logger.info("Removing large directory trees...")
    large_directories = ["/opt/ghc", "/usr/local/lib/android", "/usr/share/dotnet/"]
    for directory in large_directories:
        subprocess.run(["sudo", "rm", "-rf", directory], check=False)

    # Show available space after cleanup
    logger.info("Current disk space after cleanup:")
    subprocess.run(["df", "-h"], check=False)


def init_logger():
    """Initializes and configures a logger for build operations."""
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
    """Retrieves an environment variable, returning default if provided, else raises ValueError."""
    value = os.environ.get(var_name, default)
    if value is None:
        raise ValueError(f"Environment variable '{var_name}' is not set")
    return value


def collect_system_metrics():
    """Collects system metrics to diagnose resource-related build failures."""
    metrics = {}

    # Process list helps identify resource contention
    try:
        metrics["processes"] = subprocess.check_output(
            ["ps", "aux"], stderr=subprocess.STDOUT, timeout=5
        ).decode()
    except Exception as e:
        metrics["processes_error"] = str(e)

    # CPU metrics help identify compute bottlenecks
    try:
        metrics["cpu"] = subprocess.check_output(
            ["top", "-bn1"], stderr=subprocess.STDOUT, timeout=5
        ).decode()
    except Exception as e:
        metrics["cpu_error"] = str(e)

    # Memory metrics help identify memory pressure
    try:
        metrics["memory"] = subprocess.check_output(
            ["free", "-m"], stderr=subprocess.STDOUT, timeout=5
        ).decode()
    except Exception as e:
        metrics["memory_error"] = str(e)

    # Disk metrics help identify storage issues
    try:
        metrics["disk"] = subprocess.check_output(
            ["df", "-h"], stderr=subprocess.STDOUT, timeout=5
        ).decode()
    except Exception as e:
        metrics["disk_error"] = str(e)

    return metrics


def log_system_metrics(metrics: dict | None = None):
    """Logs system metrics using the global logger with error handling.

    Args:
        metrics: Dictionary containing system metrics data. If None, function exits early.
    """
    if not metrics:
        return

    if "processes" in metrics:
        logger.error("Running processes at failure time:\n%s", metrics["processes"])
    else:
        logger.error(
            "Failed to collect process list: %s",
            metrics.get("processes_error", "Unknown error"),
        )

    if "cpu" in metrics:
        logger.error("CPU utilization at failure time:\n%s", metrics["cpu"])
    else:
        logger.error(
            "Failed to collect CPU metrics: %s",
            metrics.get("cpu_error", "Unknown error"),
        )

    if "memory" in metrics:
        logger.error("Memory status at failure time:\n%s", metrics["memory"])
    else:
        logger.error(
            "Failed to collect memory metrics: %s",
            metrics.get("memory_error", "Unknown error"),
        )

    if "disk" in metrics:
        logger.error("Disk usage at failure time:\n%s", metrics["disk"])
    else:
        logger.error(
            "Failed to collect disk metrics: %s",
            metrics.get("disk_error", "Unknown error"),
        )


def build_and_push_image(build_args) -> BuildResult:
    """Builds and pushes a Docker image with retry logic and metrics collection."""
    (
        dockerfile_path,
        base_image,
        date_str,
        date_time_str,
        commit_hash,
        max_retries,
        logger_lock,
        docker_platform,
    ) = build_args

    # Derive image name from parent directory of Dockerfile
    directory_path = os.path.dirname(dockerfile_path)
    image_name_dir = os.path.basename(directory_path).lower()

    # Construct tags with multiple variants for deployment flexibility
    tags = [
        f"{base_image}:{image_name_dir}.{docker_platform}",
        f"{base_image}:{image_name_dir}.latest.{docker_platform}",
        f"{base_image}:{image_name_dir}.{date_str}.{docker_platform}",
        f"{base_image}:{image_name_dir}.{date_time_str}.{docker_platform}",
        f"{base_image}:{image_name_dir}.{commit_hash}.{docker_platform}",
        f"{base_image}:{image_name_dir}.{commit_hash}.{date_str}.{docker_platform}",
        f"{base_image}:{image_name_dir}.{commit_hash}.{date_time_str}.{docker_platform}",
    ]

    builder_name = f"builder_{image_name_dir}"

    # Build command with multi-platform support
    buildx_command = [
        "docker",
        "buildx",
        "build",
        "--output",
        "type=registry,compression=zstd,force-compression=true,compression-level=3,rewrite-timestamp=true,oci-mediatypes=true",
        "--no-cache",
        "--builder",
        builder_name,
        "--platform",
        f"linux/{docker_platform}",
    ]
    for tag in tags:
        buildx_command.extend(["--tag", tag])
    buildx_command.extend(["--file", dockerfile_path, directory_path])

    # Builder management commands
    create_builder_command = ["docker", "buildx", "create", "--name", builder_name]
    remove_builder_command = ["docker", "buildx", "rm", builder_name]

    try:
        error_msg = "Unknown error"
        subprocess.run(create_builder_command, check=True)

        for attempt in range(1, max_retries + 1):
            try:
                with logger_lock:
                    logger.info(
                        f"Attempting build for image '{tags[0]}' (attempt {attempt} of {max_retries}) with tags:"
                    )
                    for tag in tags:
                        logger.info(f"  - {tag}")
                subprocess.run(buildx_command, check=True)
                return BuildResult(
                    image_name=tags[0],
                    success=True,
                    attempts=attempt,
                )
            except BaseException as e:
                error_msg = str(e)
                with logger_lock:
                    logger.warning(
                        f"Build attempt {attempt} of {max_retries} failed for image '{tags[0]}': {e}",
                        exc_info=True,
                    )

        with logger_lock:
            logger.error(
                f"All {max_retries} build attempts failed for image '{tags[0]}'."
            )
        return BuildResult(
            image_name=tags[0],
            success=False,
            attempts=max_retries,
            error_msg=error_msg,
            system_metrics=collect_system_metrics(),
        )

    finally:
        subprocess.run(remove_builder_command, check=False)


def main():
    global logger

    logger = init_logger()

    # Free up disk space before starting builds
    free_disk_space()

    # Configuration from environment variables
    docker_registry = get_env_var("DOCKER_REGISTRY").lower()
    docker_image_name = get_env_var("DOCKER_IMAGE_NAME").lower()
    docker_platform = get_env_var("DOCKER_PLATFORM", "amd64").lower()
    max_retries = int(get_env_var("MAX_RETRIES", "3"))
    github_sha = get_env_var("GITHUB_SHA")

    # Timestamping for image tags
    date_str = get_env_var("DATE_STR")
    date_time_str = get_env_var("DATE_TIME_STR")
    base_image = f"{docker_registry}/{docker_image_name}"

    logger.info(f"Initializing build process with base image path: {base_image}")

    # Locate Dockerfiles in current directory tree
    dockerfiles = glob.glob("**/Dockerfile", recursive=True)
    if not dockerfiles:
        logger.error("No Dockerfiles found in the current directory or subdirectories.")
        sys.exit(1)

    # Sort for consistent processing order
    dockerfiles.sort(reverse=True)
    logger.info(f"Found {len(dockerfiles)} Dockerfiles:")
    for dockerfile in dockerfiles:
        logger.info(f"  - {dockerfile}")

    # Prepare parallel build arguments
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
            docker_platform,
        )
        args_list.append(args)

    # Execute parallel builds using process pool
    num_processes = multiprocessing.cpu_count()
    logger.info(
        f"Parallel builds initiated with {num_processes} worker processes (based on available CPUs)."
    )

    with multiprocessing.Pool(processes=num_processes) as pool:
        results = pool.map(build_and_push_image, args_list)

    # Process build results
    failed_builds = [result for result in results if not result.success]

    if failed_builds:
        logger.error("Build failures detected:")
        for failure in failed_builds:
            logger.error(
                f"Image '{failure.image_name}' failed after {failure.attempts} attempts. Last error: {failure.error_msg}"
            )
            logger.error("System status during failure:")
            log_system_metrics(failure.system_metrics)
        sys.exit(1)
    else:
        logger.info("All Docker builds completed successfully.")


if __name__ == "__main__":
    main()
