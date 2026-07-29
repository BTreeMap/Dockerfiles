"""Provisioning and lifetime management for cloudflared quick tunnels.

Two things this module refuses to do implicitly. It does not download from
`releases/latest`, because a build that resolves a different binary on every run
is not reproducible and gives nothing to compare against. And it does not accept
whatever bytes arrive: the digest is checked before the file is made executable.

Cloudflare publishes no checksum manifest for these assets, so the digests below
were computed once from the pinned release and committed. That is
trust-on-first-use rather than an upstream signature -- it cannot tell you the
original upload was honest, but it does detect the asset changing underneath a
pinned tag, which is the failure this guards against.

Bumping the version means updating VERSION and both digests together.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from ci.domain import Hostname, Platform, TunnelReady, TunnelStatus, TunnelUnavailable

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

_DOWNLOAD_CHUNK_BYTES = 1 << 20


@dataclass(frozen=True, slots=True)
class Installed:
    path: Path
    version: str


@dataclass(frozen=True, slots=True)
class InstallFailed:
    reason: str


InstallOutcome = Installed | InstallFailed


def _digest_of(path: Path) -> str:
    """Streams the file so a ~40 MB binary never lands in memory whole."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def install(
    platform: Platform,
    destination: Path,
    version: str = VERSION,
    timeout_seconds: float = 180.0,
) -> InstallOutcome:
    """Fetches, verifies, and installs cloudflared, reusing a matching binary.

    The digest check happens on a temporary path; the file is moved into place
    only once it is proven, so a torn or tampered download can never be executed
    and a failed run leaves nothing half-installed behind.
    """
    expected = _DIGESTS.get(platform)
    if expected is None:
        return InstallFailed(f"no pinned digest for platform {platform}")

    if destination.exists() and _digest_of(destination) == expected:
        logger.info("cloudflared %s already present and verified", version)
        return Installed(path=destination, version=version)

    url = _RELEASE_URL.format(version=version, arch=platform)
    staging = destination.with_suffix(".partial")
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=timeout_seconds
        ) as response:
            response.raise_for_status()
            with staging.open("wb") as handle:
                for chunk in response.iter_bytes(_DOWNLOAD_CHUNK_BYTES):
                    handle.write(chunk)
    except (httpx.HTTPError, OSError) as error:
        staging.unlink(missing_ok=True)
        return InstallFailed(f"download failed: {error}")

    actual = _digest_of(staging)
    if actual != expected:
        staging.unlink(missing_ok=True)
        return InstallFailed(f"digest mismatch: expected {expected}, got {actual}")

    staging.chmod(staging.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    staging.replace(destination)
    logger.info("Installed cloudflared %s (digest verified)", version)
    return Installed(path=destination, version=version)


def resolve_binary(platform: Platform, cache_dir: Path | None = None) -> InstallOutcome:
    """Returns a usable cloudflared, preferring one already on PATH."""
    on_path = shutil.which("cloudflared")
    if on_path is not None:
        return Installed(path=Path(on_path), version="preinstalled")

    root = cache_dir or Path(os.environ.get("RUNNER_TOOL_CACHE", "/tmp")) / "cloudflared"
    return install(platform, root / f"cloudflared-{VERSION}")


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
