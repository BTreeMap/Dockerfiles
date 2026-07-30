"""Provisioning and lifetime management for cloudflared quick tunnels.

One thing this module refuses to do implicitly: it does not download from
`releases/latest`, because a build that resolves a different binary on every run
is not reproducible and gives nothing to compare against. Fetching and verifying
the pinned asset is `ci.assets`' job, and the trust-on-first-use reasoning behind
the committed digests is documented there.

Bumping the version means updating VERSION and both digests together.

What remains here is the part that is actually about tunnels: scraping the
hostname cloudflared announces, and owning the child process for exactly as long
as the block that asked for it.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ci.assets import Encoding, resolve
from ci.domain import (
    Hostname,
    InstallOutcome,
    Platform,
    TunnelReady,
    TunnelStatus,
    TunnelUnavailable,
)

logger = logging.getLogger("ci.tunnel")

VERSION = "2026.7.3"

_DIGESTS: dict[Platform, str] = {
    Platform.AMD64: "9d71c677db00134c1bd4144b7783486b654ad281b1ea62b4972098d19f770f17",
    Platform.ARM64: "65259e652a7bea08bf5df603233ab22b8bf3116af8df9f9206209af6a1b955c0",
}

_RELEASE_URL = (
    "https://github.com/cloudflare/cloudflared/releases/download/{version}/cloudflared-linux-{arch}"
)

# cloudflared announces the quick tunnel once, on stderr, in a banner.
_TUNNEL_URL = re.compile(r"https://(?P<host>[a-z0-9-]+(?:\.[a-z0-9-]+)*\.trycloudflare\.com)")

def resolve_binary(platform: Platform, cache_dir: Path | None = None) -> InstallOutcome:
    """Returns a usable cloudflared, preferring one already on PATH."""
    root = cache_dir or Path(os.environ.get("RUNNER_TOOL_CACHE", "/tmp")) / "cloudflared"
    return resolve(
        name="cloudflared",
        url=_RELEASE_URL.format(version=VERSION, arch=platform),
        expected_digest=_DIGESTS.get(platform),
        destination=root / f"cloudflared-{VERSION}",
        encoding=Encoding.RAW,
        version=VERSION,
    )


@contextmanager
def quick_tunnel(
    binary: Path,
    local_port: int,
    startup_timeout_seconds: float = 60.0,
) -> Iterator[TunnelStatus]:
    """Runs a quick tunnel for the duration of the block.

    A context manager rather than start/stop methods so the child process cannot
    outlive its scope: the previous shape leaked cloudflared if the worker raised
    between starting the tunnel and reaching its `finally`.

    Output is captured rather than inherited. On a public repository the
    workflow log is world-readable in real time, and cloudflared prints the
    tunnel URL on startup; capturing it keeps the hostname out of the log so the
    HMAC is the second line of defence rather than the only one.
    """
    process = subprocess.Popen(
        [str(binary), "tunnel", "--url", f"http://127.0.0.1:{local_port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        yield _await_hostname(process, deadline=time.monotonic() + startup_timeout_seconds)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _await_hostname(process: subprocess.Popen[str], deadline: float) -> TunnelStatus:
    """Scrapes the announced hostname, then keeps draining the pipe.

    Draining matters: if nobody reads cloudflared's output the pipe buffer fills
    and the process blocks, taking the tunnel down mid-run.
    """
    found: list[Hostname] = []

    def drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if found:
                continue
            match = _TUNNEL_URL.search(line)
            if match is not None:
                hostname = Hostname.parse(match.group("host"))
                if hostname is not None:
                    found.append(hostname)

    threading.Thread(target=drain, daemon=True, name="cloudflared-drain").start()

    while time.monotonic() < deadline:
        if found:
            logger.info("Quick tunnel established (hostname withheld from logs)")
            return TunnelReady(hostname=found[0])
        if process.poll() is not None:
            return TunnelUnavailable(f"cloudflared exited with code {process.returncode}")
        time.sleep(0.25)

    return TunnelUnavailable("cloudflared did not announce a hostname before the deadline")
