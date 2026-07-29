#!/usr/bin/env python3

"""
Mesh worker: builds the tasks it was dealt, and steals more when it runs dry.

The scheduling model is deliberately minimal. Each worker starts with a disjoint
share of the run's tasks, so it makes progress immediately without talking to
anyone. Coordination only happens on the idle path, and every part of it is
allowed to fail: an unreachable peer costs a missed steal, never a missed build.
The reconcile stage is what turns that into a guarantee.
"""


import json
import logging
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass

from mesh import MeshClient, MeshServer, Task, TaskQueue, run_worker


@dataclass
class BuildResult:
    """Tracks the outcome of a Docker image build attempt."""

    image_name: str
    success: bool
    attempts: int
    duration_seconds: float
    stolen: bool = False
    error_msg: str | None = None
    system_metrics: dict | None = None


def _compute_backoff_seconds(
    attempt: int,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 60.0,
) -> float:
    """
    Capped exponential backoff with full jitter:
      sleep = random_between(0, min(max_delay, base_delay * 2^(attempt-1)))
    """
    cap = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
    return random.uniform(0.0, cap)


def remove_packages(pkg_patterns: list[str]) -> None:
    """
    Removes packages matching the given patterns using dpkg and apt.

    Args:
        pkg_patterns: List of package name patterns, supporting globbing.
    """
    try:
        all_packages: list[str] = []
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


def free_disk_space() -> None:
    """
    Removes unnecessary packages and directories to free up disk space for Docker builds.

    Still worth its runtime: a worker runs cpu_count() builds concurrently, and
    several of these images write multi-gigabyte layers, so disk -- not CPU -- is
    the resource that actually collides on a runner.
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


def init_logger() -> logging.Logger:
    """Initializes and configures a logger for build operations."""
    logger_obj = logging.getLogger("docker_builder")
    logger_obj.setLevel(logging.INFO)

    # Prevent duplicate handlers if init_logger() is called multiple times.
    if logger_obj.handlers:
        return logger_obj

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s][%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d.%H-%M-%S",
    )
    handler.setFormatter(formatter)
    logger_obj.addHandler(handler)

    # Avoid duplication via root logger propagation in some environments.
    logger_obj.propagate = False
    return logger_obj


def get_env_var(var_name: str, default: str | None = None) -> str:
    """Retrieves an environment variable, returning default if provided, else raises ValueError."""
    value = os.environ.get(var_name, default)
    if value is None:
        raise ValueError(f"Environment variable '{var_name}' is not set")
    return value


def collect_system_metrics() -> dict[str, str]:
    """Collects system metrics to diagnose resource-related build failures."""
    metrics: dict[str, str] = {}

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


def log_system_metrics(metrics: dict[str, str] | None = None) -> None:
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


def build_tags(task: Task, base_image: str, date_str: str, date_time_str: str, commit_hash: str) -> list[str]:
    """Constructs the tag variants published for a single platform build."""
    return [
        f"{base_image}:{task.image}.{task.platform}",
        f"{base_image}:{task.image}.latest.{task.platform}",
        f"{base_image}:{task.image}.{date_str}.{task.platform}",
        f"{base_image}:{task.image}.{date_time_str}.{task.platform}",
        f"{base_image}:{task.image}.{commit_hash}.{task.platform}",
        f"{base_image}:{task.image}.{commit_hash}.{date_str}.{task.platform}",
        f"{base_image}:{task.image}.{commit_hash}.{date_time_str}.{task.platform}",
    ]


def build_and_push_image(
    task: Task,
    base_image: str,
    date_str: str,
    date_time_str: str,
    commit_hash: str,
) -> BuildResult:
    """Builds and pushes a Docker image with retry logic and metrics collection."""
    tags = build_tags(task, base_image, date_str, date_time_str, commit_hash)

    # Namespaced by platform as well as image: a stolen task may land on a worker
    # that is already building the same image for the other platform.
    builder_name = f"builder_{task.image}_{task.platform}"

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
        f"linux/{task.platform}",
    ]
    for tag in tags:
        buildx_command.extend(["--tag", tag])
    buildx_command.extend(["--file", task.dockerfile, task.context])

    create_builder_command = ["docker", "buildx", "create", "--name", builder_name]
    remove_builder_command = ["docker", "buildx", "rm", builder_name]

    started_at = time.monotonic()

    try:
        error_msg = "Unknown error"
        subprocess.run(create_builder_command, check=True)

        max_retries = task.max_retries
        unlimited_retries = max_retries <= 0
        attempt = 0

        while unlimited_retries or attempt < max_retries:
            attempt += 1
            try:
                if unlimited_retries:
                    attempt_suffix = f"{attempt}/∞"
                else:
                    attempt_suffix = f"{attempt}/{max_retries}"

                logger.info(
                    f"Attempting build for image '{tags[0]}' (attempt {attempt_suffix}) with tags:"
                )
                for tag in tags:
                    logger.info(f"  - {tag}")

                subprocess.run(buildx_command, check=True)
                return BuildResult(
                    image_name=tags[0],
                    success=True,
                    attempts=attempt,
                    duration_seconds=time.monotonic() - started_at,
                )

            except BaseException as e:
                error_msg = str(e)
                logger.warning(
                    f"Build attempt {attempt} of {max_retries} failed for image '{tags[0]}': {e}",
                    exc_info=True,
                )

                if (not unlimited_retries) and attempt >= max_retries:
                    break

                time.sleep(_compute_backoff_seconds(attempt=attempt))

        logger.error(f"All {max_retries} build attempts failed for image '{tags[0]}'.")

        return BuildResult(
            image_name=tags[0],
            success=False,
            attempts=attempt,
            duration_seconds=time.monotonic() - started_at,
            error_msg=error_msg,
            system_metrics=collect_system_metrics(),
        )

    finally:
        subprocess.run(remove_builder_command, check=False)


def write_summary(results: list[BuildResult], worker_id: int) -> None:
    """Emits per-image timings and steal outcomes to the job summary.

    Durations are recorded not to feed a scheduler -- the mesh needs no cost
    estimates -- but so the tail image is visible, since that is the floor no
    amount of parallelism can go below.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = [
        f"### Worker {worker_id}",
        "",
        "| Image | Result | Attempts | Duration | Origin |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in sorted(results, key=lambda r: -r.duration_seconds):
        status = "ok" if result.success else "**failed**"
        origin = "stolen" if result.stolen else "dealt"
        lines.append(
            f"| `{result.image_name.split(':')[-1]}` | {status} | {result.attempts} "
            f"| {result.duration_seconds / 60:.1f} min | {origin} |"
        )
    lines.append("")

    with open(summary_path, "a") as handle:
        handle.write("\n".join(lines))


def main() -> None:
    global logger

    logger = init_logger()
    logging.getLogger("mesh").addHandler(logger.handlers[0])
    logging.getLogger("mesh").setLevel(logging.INFO)
    logging.getLogger("mesh").propagate = False

    free_disk_space()

    docker_registry = get_env_var("DOCKER_REGISTRY").lower()
    docker_image_name = get_env_var("DOCKER_IMAGE_NAME").lower()
    github_sha = get_env_var("GITHUB_SHA")
    date_str = get_env_var("DATE_STR")
    date_time_str = get_env_var("DATE_TIME_STR")
    base_image = f"{docker_registry}/{docker_image_name}"

    worker_id = int(get_env_var("WORKER_ID"))
    platform = get_env_var("DOCKER_PLATFORM")
    mesh_secret = get_env_var("MESH_SECRET")
    repository = get_env_var("GITHUB_REPOSITORY")
    run_id = get_env_var("GITHUB_RUN_ID")
    token = get_env_var("GITHUB_TOKEN")

    tasks = [Task.from_dict(entry) for entry in json.loads(get_env_var("WORKER_TASKS"))]
    initial_images = {task.image for task in tasks}

    logger.info(
        f"Worker {worker_id} ({platform}) dealt {len(tasks)} task(s): "
        f"{', '.join(sorted(initial_images)) or 'none'}"
    )

    queue = TaskQueue(tasks)

    server = MeshServer(worker_id=worker_id, secret=mesh_secret, queue=queue)
    hostname = server.start()

    client = MeshClient(
        secret=mesh_secret,
        worker_id=worker_id,
        repository=repository,
        run_id=run_id,
        platform=platform,
        token=token,
    )
    if hostname:
        client.publish(hostname, github_sha)

    collected: list[BuildResult] = []

    def execute(task: Task) -> bool:
        result = build_and_push_image(
            task=task,
            base_image=base_image,
            date_str=date_str,
            date_time_str=date_time_str,
            commit_hash=github_sha,
        )
        result.stolen = task.image not in initial_images
        collected.append(result)
        return result.success

    # cpu_count() concurrent builds: Docker builds here are dominated by network
    # fetches and layer I/O rather than compute, so oversubscribing the cores is
    # what actually raises throughput.
    slots = max(1, os.cpu_count() or 1)

    try:
        run_worker(
            queue=queue,
            client=client,
            execute=execute,
            slots=slots,
            expected_peers=int(get_env_var("WORKER_COUNT", "4")) - 1,
        )
    finally:
        server.stop()

    write_summary(collected, worker_id)

    failed_builds = [result for result in collected if not result.success]

    logger.info(
        f"Worker {worker_id} completed {len(collected)} build(s), "
        f"{len(failed_builds)} failed."
    )

    if failed_builds:
        logger.error("Build failures detected:")
        for failure in failed_builds:
            logger.error(
                f"Image '{failure.image_name}' failed after {failure.attempts} attempts. "
                f"Last error: {failure.error_msg}"
            )
            logger.error("System status during failure:")
            log_system_metrics(failure.system_metrics)
        sys.exit(1)
    else:
        logger.info("All Docker builds on this worker completed successfully.")


if __name__ == "__main__":
    main()
