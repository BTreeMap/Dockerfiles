#!/usr/bin/env python3

"""
Discovers build tasks and deals them out to the mesh workers.

This is the single source of truth for the work list. Both the build and
manifest stages consume this output instead of globbing independently, so the
two can no longer disagree about which images exist.

Tasks are dealt at random rather than by estimated cost. Build contexts here are
a few kilobytes each -- these images fetch everything from the network -- so
context size carries no signal about build duration, and no other cost proxy is
available without maintaining a measured duration table. Random dealing plus
work stealing gets the same result with nothing to maintain.
"""


import glob
import json
import os
import random
import secrets
import sys
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Task:
    """Mirrors mesh.Task; kept as a plain dataclass so this script has no imports
    from the build path and can run on a bare runner."""

    image: str
    dockerfile: str
    context: str
    platform: str
    max_retries: int


def get_env_var(var_name: str, default: str | None = None) -> str:
    value = os.environ.get(var_name, default)
    if value is None:
        raise ValueError(f"Environment variable '{var_name}' is not set")
    return value


def discover_tasks(platforms: list[str], max_retries: int) -> list[Task]:
    """Builds one task per (image, platform)."""
    dockerfiles = sorted(glob.glob("**/Dockerfile", recursive=True))
    if not dockerfiles:
        print("No Dockerfiles found in the current directory or subdirectories.")
        sys.exit(1)

    tasks: list[Task] = []
    for dockerfile in dockerfiles:
        context = os.path.dirname(dockerfile)
        image = os.path.basename(context).lower()
        for platform in platforms:
            tasks.append(
                Task(
                    image=image,
                    dockerfile=dockerfile,
                    context=context,
                    platform=platform,
                    max_retries=max_retries,
                )
            )
    return tasks


def deal(tasks: list[Task], worker_count: int, seed: int) -> list[list[Task]]:
    """Splits tasks into `worker_count` disjoint shares.

    Disjoint ownership from the start is what keeps the mesh optional: every
    task has exactly one initial owner that will build it if nobody steals it,
    so an unreachable peer degrades the run to plain static partitioning rather
    than dropping work.
    """
    shuffled = list(tasks)
    random.Random(seed).shuffle(shuffled)
    shares: list[list[Task]] = [[] for _ in range(worker_count)]
    for index, task in enumerate(shuffled):
        shares[index % worker_count].append(task)
    return shares


def write_output(name: str, value: str) -> None:
    with open(get_env_var("GITHUB_OUTPUT"), "a") as handle:
        if "\n" in value:
            handle.write(f"{name}<<__EOF__\n{value}\n__EOF__\n")
        else:
            handle.write(f"{name}={value}\n")


def main() -> None:
    platforms = get_env_var("PLATFORMS", "amd64,arm64").split(",")
    worker_count = int(get_env_var("WORKER_COUNT", "4"))
    max_retries = int(get_env_var("MAX_RETRIES", "50"))

    tasks = discover_tasks(platforms, max_retries)
    print(f"Discovered {len(tasks)} tasks across {len(platforms)} platform(s).")

    # One shared secret for the whole run, masked so it never reaches the log.
    # Scoped to a single run and dying with the tunnels, so there is nothing to
    # rotate and no repository secret to provision.
    mesh_secret = secrets.token_hex(32)
    print(f"::add-mask::{mesh_secret}")

    matrix_entries: list[dict[str, object]] = []
    for platform in platforms:
        platform_tasks = [task for task in tasks if task.platform == platform]
        # Seed per platform so the two platforms get independent deals; a slow
        # image then lands on differently-loaded workers on each side.
        shares = deal(platform_tasks, worker_count, seed=hash(platform) & 0xFFFFFFFF)
        for worker_id, share in enumerate(shares):
            matrix_entries.append(
                {
                    "platform": platform,
                    "worker_id": worker_id,
                    "runner": "ubuntu-24.04" if platform == "amd64" else "ubuntu-24.04-arm",
                    "tasks": [asdict(task) for task in share],
                }
            )
            print(
                f"  {platform} worker {worker_id}: "
                f"{', '.join(task.image for task in share)}"
            )

    write_output("matrix", json.dumps({"include": matrix_entries}))
    write_output("mesh_secret", mesh_secret)
    write_output("images", json.dumps(sorted({task.image for task in tasks})))
    write_output("platforms", json.dumps(platforms))


if __name__ == "__main__":
    main()
