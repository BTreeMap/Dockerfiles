#!/usr/bin/env python3

"""Entry point: discover the run's tasks, deal them, and emit the build matrix."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from ci.discovery import ConflictingDockerfiles, MatrixEntry, deal, discover, seed_for
from ci.domain import Platform
from ci.env import (
    COUNT,
    RETRIES,
    TEXT,
    BuildIdentity,
    read,
    registry_repository,
    write_output,
    write_summary,
)
from ci.logs import configure
from ci.provenance import generations
from ci.references import CyclicGraph, DanglingReference, MisdeclaredReference
from ci.report import graph_section, run_section

logger = logging.getLogger("ci.discover")


def main() -> int:
    configure()

    platforms = tuple(
        filter(None, map(Platform.parse, read("PLATFORMS", TEXT, default="amd64,arm64").split(",")))
    )
    if not platforms:
        logger.error("No valid platforms configured.")
        return 1

    # Bounded here so a misconfigured matrix is an explained environment error
    # rather than the bare ValueError `deal` would otherwise raise from inside
    # a comprehension.
    worker_count = read("WORKER_COUNT", COUNT, default=4)

    try:
        discovered = discover(Path.cwd(), platforms, read("MAX_RETRIES", RETRIES, default=50))
    # Every one of these is a layout defect the tree states and only the whole
    # tree can detect, so all are reported here and refuse the run rather than
    # being carried into a build that would publish something arbitrary.
    except (
        ConflictingDockerfiles,
        CyclicGraph,
        DanglingReference,
        MisdeclaredReference,
    ) as defect:
        logger.error("%s", defect)
        return 1

    tasks = discovered.tasks
    if not tasks:
        logger.error("No Dockerfiles found in the current directory or subdirectories.")
        return 1

    logger.info("Discovered %d tasks across %d platform(s).", len(tasks), len(platforms))

    entries = tuple(
        MatrixEntry(platform=platform, worker_id=worker_id, tasks=share)
        for platform in platforms
        for worker_id, share in enumerate(
            deal(
                tuple(task for task in tasks if task.platform is platform),
                worker_count,
                seed_for(platform),
            )
        )
    )

    for entry in entries:
        logger.info("  %s worker %d: %s", entry.platform, entry.worker_id, entry.summary)

    write_output("matrix", json.dumps({"include": [entry.as_json() for entry in entries]}))

    # Reconcile fans out the same way the build stage does -- one job per
    # architecture, on a runner of that architecture -- so a rebuild never falls
    # back to binfmt/QEMU emulation. Emitted from the same `platforms` tuple and
    # the same `runner_label` as the build matrix above, so the two stages
    # cannot drift onto different runner shapes.
    write_output(
        "reconcile_matrix",
        json.dumps(
            {
                "include": [
                    {"platform": str(platform), "runner": platform.runner_label}
                    for platform in platforms
                ]
            }
        ),
    )

    # The graph comes out of the same read the tasks did. It is a fact about the
    # tree rather than about this run -- identical between two runs unless a
    # Dockerfile moved, which is what makes any difference in it worth a second
    # look -- so it is reported here, once, from the only stage that sees it all.
    found = discovered.graph
    probe = found.probe
    table = (
        generations(
            probe=probe[0],
            base=probe[1],
            registry_repository=registry_repository(),
            platform=platforms[0],
            depth=found.depth,
        )
        if probe is not None
        else ()
    )
    write_summary(
        [
            *run_section(
                identity=BuildIdentity.from_environment(),
                generations=table,
                needed=found.depth,
                probe=probe[0] if probe else None,
                images=len({task.image for task in tasks}),
                platforms=len(platforms),
            ),
            *graph_section(found, resolved=len(table)),
        ]
    )

    # Walked once here so every builder pins against one answer: the plan job is
    # the only stage that runs once per run, and a table computed per worker could
    # differ between them mid-run.
    write_output("generations", ",".join(str(batch) for batch in table))

    write_output("images", json.dumps(sorted({task.image for task in tasks})))
    write_output("platforms", json.dumps([str(platform) for platform in platforms]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
