#!/usr/bin/env python3

"""Entry point: discover the run's tasks, deal them, and emit the build matrix."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from ci.discovery import deal, discover, seed_for
from ci.domain import Platform
from ci.env import optional, require_int, write_output
from ci.logs import configure

logger = logging.getLogger("ci.discover")


def main() -> int:
    configure()

    platforms = tuple(
        filter(None, map(Platform.parse, optional("PLATFORMS", "amd64,arm64").split(",")))
    )
    if not platforms:
        logger.error("No valid platforms configured.")
        return 1

    worker_count = require_int("WORKER_COUNT", 4)
    tasks = discover(Path.cwd(), platforms, require_int("MAX_RETRIES", 50))
    if not tasks:
        logger.error("No Dockerfiles found in the current directory or subdirectories.")
        return 1

    logger.info("Discovered %d tasks across %d platform(s).", len(tasks), len(platforms))

    entries = [
        {
            "platform": str(platform),
            "worker_id": worker_id,
            "runner": platform.runner_label,
            "tasks": [task.as_json() for task in share],
        }
        for platform in platforms
        for worker_id, share in enumerate(
            deal(
                tuple(task for task in tasks if task.platform is platform),
                worker_count,
                seed_for(platform),
            )
        )
    ]

    for entry in entries:
        logger.info(
            "  %s worker %s: %s",
            entry["platform"],
            entry["worker_id"],
            ", ".join(task["image"] for task in entry["tasks"]) or "(none)",
        )

    write_output("matrix", json.dumps({"include": entries}))
    write_output("images", json.dumps(sorted({task.image for task in tasks})))
    write_output("platforms", json.dumps([str(platform) for platform in platforms]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
