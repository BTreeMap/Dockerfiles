#!/usr/bin/env python3

"""Entry point: give this runner a clean egress path, or leave it as it found it.

Provisions the WARP tunnel and local proxy described in `ci/egress.py`, then
hands the result to the rest of the job through `$GITHUB_ENV`. Steps that follow
read `BUILD_PROXY_URL` and route builds through it.

This step cannot fail the job, by construction. `main` catches everything and
always exits 0, and the workflow marks the step `continue-on-error` on top of
that -- two layers, because the Python guard cannot save a runner that OOM-kills
the interpreter or an `uv` that fails to resolve.

That is not defensiveness for its own sake. Every dependency here is external
and occasionally down: GitHub's release CDN, Cloudflare's registration API,
Cloudflare's MASQUE edge. This exists to route *around* a degraded network, so
letting it fail the run would mean the mitigation for a bad day became a new way
to lose a good one. When any of it is unavailable the build simply egresses from
the runner's own address, which is what it does today.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from ci.domain import ProxyReady, ProxyUnavailable
from ci.egress import (
    DEFAULT_PROXY_PORT,
    MasqueNode,
    RegistrationFailed,
    host_platform,
    register,
    resolve_binary,
    start_proxy,
)
from ci.env import mask, optional, require_int, write_env
from ci.logs import configure
from ci.tunnel import Installed, InstallFailed

logger = logging.getLogger("ci.egress.setup")

# Every degradation reads the same way on purpose: the outcome is identical
# whichever stage gave up, and it is the one this runner would have had anyway.
_DEGRADED = "%s (%s); builds will use the runner's own path"


def provision() -> None:
    """Sets up clean egress, or explains in one line why it did not."""
    platform = host_platform()
    if platform is None:
        logger.warning(_DEGRADED, "unsupported runner architecture", "no mihomo build")
        return

    port = require_int("EGRESS_PROXY_PORT", DEFAULT_PROXY_PORT)
    working_dir = Path(optional("RUNNER_TEMP", "/tmp")) / "egress"

    match resolve_binary(platform):
        case InstallFailed(reason):
            logger.warning(_DEGRADED, "mihomo unavailable", reason)
            return
        case Installed(binary, version):
            logger.info("Using mihomo %s", version)

    match register():
        case RegistrationFailed(reason):
            logger.warning(_DEGRADED, "WARP registration failed", reason)
            return
        case MasqueNode() as node:
            # The private key reaches a config file on disk and nothing else,
            # but registering it for redaction costs nothing and closes the gap
            # if a future traceback ever carries it into the log.
            mask(node.private_key)
            logger.info("Registered an ephemeral WARP device")

    match start_proxy(binary, node, working_dir, port=port):
        case ProxyUnavailable(reason):
            logger.warning(_DEGRADED, "proxy did not come up", reason)
        case ProxyReady(local_url, container_url):
            # Only the container-facing address is exported. Host tooling is
            # deliberately left alone: registry pushes and the Actions control
            # plane are the critical path, and putting a userspace hop in front
            # of multi-gigabyte layer uploads to fix an unrelated upstream is
            # the wrong trade.
            write_env("BUILD_PROXY_URL", container_url)
            logger.info("Clean egress ready; builds will route through %s", container_url)
            logger.debug("Proxy also reachable from the runner at %s", local_url)


def main() -> int:
    configure()
    try:
        provision()
    except Exception:
        # Deliberately bare. The point is not to enumerate what can go wrong out
        # there -- it is that *nothing* going wrong out there may cost this run
        # its build. The traceback is logged because a silent skip that nobody
        # can diagnose is its own failure mode.
        logger.warning("Egress setup raised; builds will use the runner's own path", exc_info=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
