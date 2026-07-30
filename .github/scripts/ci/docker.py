"""Docker build, push, and registry inspection.

The tag algebra and the backoff schedule are pure functions over the domain; the
only effects are the subprocess calls that run buildx. Splitting them that way
means the tag set a run will publish can be asserted in a test without Docker
being installed at all.
"""

from __future__ import annotations

import logging
import os
import random
import subprocess
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from ci.domain import BuildFailed, BuildOutcome, BuildSucceeded, Task
from ci.env import BuildIdentity

logger = logging.getLogger("ci.docker")

_METRIC_COMMANDS: Mapping[str, tuple[str, ...]] = {
    "processes": ("ps", "aux"),
    "cpu": ("top", "-bn1"),
    "memory": ("free", "-m"),
    "disk": ("df", "-h"),
}

_CLEANUP_PACKAGE_PATTERNS = (
    "azure-cli",
    "dotnet-*",
    "firefox",
    "golang-*",
    "llvm-*",
    "snapd",
    "temurin-*-jdk",
)

_CLEANUP_DIRECTORIES = ("/opt/ghc", "/usr/local/lib/android", "/usr/share/dotnet")


def backoff_seconds(
    attempt: int,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 60.0,
    uniform: object = None,
) -> float:
    """Capped exponential backoff with full jitter.

    Full jitter -- uniform over [0, cap] rather than cap itself -- is what stops
    a whole worker's slots retrying in lockstep after a shared registry blip.
    """
    draw = uniform if callable(uniform) else random.uniform
    cap = min(max_delay_seconds, base_delay_seconds * (2 ** (attempt - 1)))
    return float(draw(0.0, cap))


_PROXY_BYPASS = "localhost,127.0.0.1,::1"


def proxy_build_args(proxy_url: str | None) -> tuple[str, ...]:
    """Build arguments that route a RUN step's fetches through a local proxy.

    Pure, and the only place the proxy touches a build. `http_proxy` and its
    siblings are BuildKit *predefined* arguments: they need no `ARG` line in any
    Dockerfile, and being arguments rather than environment they do not survive
    into the published image. That is what lets a runner-level network fix stay
    invisible to all ~30 images.

    Both cases are passed because which one a program reads is not consistent --
    apt and curl take the lowercase form, parts of the Python and Go ecosystems
    the uppercase.

    An empty URL yields no arguments at all, so an unproxied run produces byte
    for byte the command it produces today.
    """
    if not proxy_url:
        return ()

    settings = {
        "http_proxy": proxy_url,
        "https_proxy": proxy_url,
        "no_proxy": _PROXY_BYPASS,
    }
    return tuple(
        argument
        for name, value in settings.items()
        for spelling in (name, name.upper())
        for argument in ("--build-arg", f"{spelling}={value}")
    )


def tags_for(task: Task, identity: BuildIdentity) -> tuple[str, ...]:
    """The full tag set a single platform build publishes.

    Pure, and the single definition of the naming scheme: reconciliation checks
    for the run-unique tag produced here rather than rebuilding the string, so
    the two cannot drift apart.
    """
    stem = f"{identity.base_image}:{task.image}"
    suffix = task.platform
    return (
        f"{stem}.{suffix}",
        f"{stem}.latest.{suffix}",
        f"{stem}.{identity.date}.{suffix}",
        f"{stem}.{identity.date_time}.{suffix}",
        f"{stem}.{identity.commit_sha}.{suffix}",
        f"{stem}.{identity.commit_sha}.{identity.date}.{suffix}",
        f"{stem}.{identity.commit_sha}.{identity.date_time}.{suffix}",
    )


def run_tag(image: str, platform: str, identity: BuildIdentity) -> str:
    """The tag that proves *this* run produced the image.

    Floating tags would still resolve to a previous day's build, so only this
    one is evidence for reconciliation.
    """
    return (
        f"{identity.base_image}:{image}.{identity.commit_sha}.{identity.date_time}.{platform}"
    )


def collect_metrics() -> dict[str, str]:
    """Samples the machine so a resource-caused failure is diagnosable later."""

    def sample(command: tuple[str, ...]) -> str:
        try:
            return subprocess.check_output(command, stderr=subprocess.STDOUT, timeout=5).decode()
        except (subprocess.SubprocessError, OSError) as error:
            return f"unavailable: {error}"

    return {name: sample(command) for name, command in _METRIC_COMMANDS.items()}


def free_disk_space() -> None:
    """Reclaims runner disk before builds start.

    Still earns its runtime: a worker runs cpu_count() builds at once and several
    of these images write multi-gigabyte layers, so disk -- not CPU -- is the
    resource that actually collides.
    """
    logger.info("Disk before cleanup:")
    subprocess.run(("df", "-h"), check=False)

    selections = (
        subprocess.run(
            ("dpkg", "--get-selections", pattern), capture_output=True, text=True, check=False
        )
        for pattern in _CLEANUP_PACKAGE_PATTERNS
    )
    installed = sorted(
        {
            line.split()[0]
            for result in selections
            if result.returncode == 0
            for line in result.stdout.splitlines()
            if line.strip()
        }
    )

    if installed:
        logger.info("Removing %d package(s):\n - %s", len(installed), "\n - ".join(installed))
        subprocess.run(("sudo", "apt-get", "remove", "--purge", "-y", *installed), check=False)
        subprocess.run(("sudo", "dpkg", "--purge", *installed), check=False)
    else:
        logger.warning("No matching packages found for removal")

    subprocess.run(("sudo", "apt-get", "autoremove", "-y"), check=False)
    subprocess.run(("sudo", "apt-get", "clean"), check=False)
    for directory in _CLEANUP_DIRECTORIES:
        subprocess.run(("sudo", "rm", "-rf", directory), check=False)

    logger.info("Disk after cleanup:")
    subprocess.run(("df", "-h"), check=False)


def tag_exists(tag: str) -> bool:
    """Asks the registry whether a tag resolves, treating errors as absent.

    Erring towards absent is the safe direction: a needless rebuild republishes
    identical content, whereas wrongly assuming presence would leave a hole.
    """
    result = subprocess.run(
        ("docker", "buildx", "imagetools", "inspect", tag), capture_output=True, check=False
    )
    return result.returncode == 0


@contextmanager
def _builder(name: str) -> Iterator[None]:
    """Owns a buildx builder for the block, removing it on every exit path."""
    subprocess.run(("docker", "buildx", "create", "--name", name), check=True)
    try:
        yield
    finally:
        subprocess.run(("docker", "buildx", "rm", name), check=False)


def build_and_push(task: Task, identity: BuildIdentity) -> BuildOutcome:
    """Builds one image, retrying to the task's own budget.

    Retrying is safe because the effect is idempotent: every attempt pushes the
    same content under the same tags, so a duplicate costs minutes and changes
    nothing observable.
    """
    tags = tags_for(task, identity)

    command = (
        "docker",
        "buildx",
        "build",
        "--output",
        "type=registry,compression=zstd,force-compression=true,"
        "compression-level=3,rewrite-timestamp=true,oci-mediatypes=true",
        "--no-cache",
        "--builder",
        # Namespaced by platform too: a stolen task can land on a worker already
        # building the same image for the other architecture.
        (builder_name := f"builder_{task.image}_{task.platform}"),
        "--platform",
        f"linux/{task.platform}",
        # Empty unless a preceding step provisioned clean egress for this
        # runner. Read here rather than threaded through Task: it is a property
        # of the machine the build lands on, not of the work itself, so a task
        # stolen by a peer correctly picks up that peer's egress and not the
        # victim's.
        *proxy_build_args(os.environ.get("BUILD_PROXY_URL")),
        *(argument for tag in tags for argument in ("--tag", tag)),
        "--file",
        task.dockerfile,
        task.context,
    )

    started = time.monotonic()
    unlimited = task.max_retries <= 0
    budget = "∞" if unlimited else str(task.max_retries)
    attempt = 0
    last_error = "no attempt was made"

    with _builder(builder_name):
        while unlimited or attempt < task.max_retries:
            attempt += 1
            logger.info("Building %s (attempt %d/%s) with tags:", tags[0], attempt, budget)
            for tag in tags:
                logger.info("  - %s", tag)

            try:
                subprocess.run(command, check=True)
                return BuildSucceeded(
                    task=task,
                    attempts=attempt,
                    duration_seconds=time.monotonic() - started,
                    started_at=started,
                )
            except (subprocess.CalledProcessError, OSError) as error:
                last_error = str(error)
                logger.warning("Attempt %d/%s failed for %s: %s", attempt, budget, tags[0], error)

            if not unlimited and attempt >= task.max_retries:
                break
            time.sleep(backoff_seconds(attempt))

    logger.error("All %d attempt(s) failed for %s", attempt, tags[0])
    return BuildFailed(
        task=task,
        attempts=attempt,
        duration_seconds=time.monotonic() - started,
        error=last_error,
        metrics=collect_metrics(),
        started_at=started,
    )
