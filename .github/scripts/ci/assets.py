"""Fetching a pinned third-party executable, verified before it can be run.

Both provisioning modules need the same thing: take a version-pinned URL and a
committed digest, prove the bytes that arrived are the bytes expected, and leave
a runnable binary at a known path -- or leave nothing at all. cloudflared ships
that asset raw and mihomo ships it gzipped, which is the *only* difference
between them, so the encoding is a parameter rather than a reason to write the
procedure twice.

Two properties hold on every path, and both are load-bearing:

*Verify before decompress.* The digest covers the bytes as downloaded, so a
substituted asset is rejected without ever being handed to a decompressor. A gzip
decoder is a far larger attack surface than a hash function, and running one over
unverified input to find out whether the input was trustworthy has the order
backwards.

*Install atomically, or not at all.* The executable bit is set on a proven file
which is then `replace`d into position -- one rename, so no observer sees a
partly-written binary, and a failed attempt leaves nothing behind for the next
run to pick up and execute.

Trust model: neither upstream publishes a checksum manifest, so the digests at
the call sites were computed once from the pinned release and committed. That is
trust-on-first-use. It cannot tell you the original upload was honest; it does
detect the asset changing underneath a pinned tag, which is the failure being
guarded against. Bumping a version means recomputing its digest.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

import httpx

from ci.domain import Installed, InstallFailed, InstallOutcome

logger = logging.getLogger("ci.assets")

# One megabyte. Large enough that a ~40 MB binary is not a syscall storm, small
# enough that nothing is ever held in memory whole.
_CHUNK_BYTES = 1 << 20


class Encoding(StrEnum):
    """How the published asset is packaged. Closed: these are the two we fetch."""

    RAW = "raw"
    GZIP = "gzip"


def digest_of(path: Path) -> str:
    """The SHA-256 of a file, streamed so its size never bounds memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp_of(destination: Path) -> Path:
    """Where the digest of the asset a cached binary came from is recorded."""
    return destination.with_suffix(".source-digest")


def _is_verified(destination: Path, expected_digest: str) -> bool:
    """Whether an existing binary is one this exact pinned asset produced.

    A provenance stamp rather than a re-hash of the binary itself, because for a
    compressed asset the two are not the same question: the committed digest
    covers the download, so a decompressed binary cannot be checked against it.
    Recording what was verified at install time answers it uniformly for both
    encodings.

    This closed a real asymmetry. The raw path re-verified its cached binary
    while the compressed path reused whatever happened to be sitting at the
    destination -- so a tampered cache was re-checked for cloudflared and
    executed unread for mihomo, under a docstring claiming neither could happen.
    """
    if not destination.exists():
        return False
    try:
        return _stamp_of(destination).read_text(encoding="utf-8").strip() == expected_digest
    except OSError:
        return False


@contextmanager
def _staging(*paths: Path) -> Iterator[None]:
    """Guarantees the temporary files are gone however the block exits.

    Every early return in here is a failure return, and each one previously had
    to remember its own cleanup -- which is exactly the kind of obligation that
    gets forgotten when a branch is added later.
    """
    try:
        yield
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def _download(url: str, destination: Path, timeout_seconds: float) -> str | None:
    """Streams `url` to `destination`. Returns a reason on failure, None on success."""
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout_seconds) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes(_CHUNK_BYTES):
                    handle.write(chunk)
    except (httpx.HTTPError, OSError) as error:
        return f"download failed: {error}"
    return None


def _decode(source: Path, destination: Path, encoding: Encoding) -> str | None:
    """Materialises the runnable file from the verified download."""
    match encoding:
        case Encoding.RAW:
            try:
                source.replace(destination)
            except OSError as error:
                return f"could not stage the download: {error}"
            return None
        case Encoding.GZIP:
            try:
                with gzip.open(source, "rb") as compressed, destination.open("wb") as handle:
                    shutil.copyfileobj(compressed, handle, _CHUNK_BYTES)
            except (OSError, gzip.BadGzipFile) as error:
                return f"decompression failed: {error}"
            return None


def install(
    url: str,
    expected_digest: str,
    destination: Path,
    encoding: Encoding = Encoding.RAW,
    version: str = "pinned",
    timeout_seconds: float = 180.0,
) -> InstallOutcome:
    """Fetches, verifies, and installs one pinned executable.

    Reuses an existing binary only when a stamp proves it came from this exact
    asset; otherwise it is replaced. Returns rather than raises, because every
    caller treats an unavailable binary as a degradation to survive rather than
    an error to propagate.
    """
    if _is_verified(destination, expected_digest):
        logger.info("%s %s already present and verified", destination.name, version)
        return Installed(path=destination, version=version)

    destination.parent.mkdir(parents=True, exist_ok=True)
    download = destination.with_suffix(".download")
    staged = destination.with_suffix(".partial")

    with _staging(download, staged):
        if (failure := _download(url, download, timeout_seconds)) is not None:
            return InstallFailed(failure)

        # Before decompression, before chmod, before anything is moved into a
        # path something else will execute.
        actual = digest_of(download)
        if actual != expected_digest:
            return InstallFailed(f"digest mismatch: expected {expected_digest}, got {actual}")

        if (failure := _decode(download, staged, encoding)) is not None:
            return InstallFailed(failure)

        try:
            staged.chmod(staged.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
            staged.replace(destination)
            # Written only after the binary is in place, so a stamp can never
            # vouch for a file that was never installed.
            _stamp_of(destination).write_text(expected_digest, encoding="utf-8")
        except OSError as error:
            return InstallFailed(f"could not install the verified binary: {error}")

    logger.info("Installed %s %s (digest verified)", destination.name, version)
    return Installed(path=destination, version=version)


def resolve(
    name: str,
    url: str,
    expected_digest: str | None,
    destination: Path,
    encoding: Encoding = Encoding.RAW,
    version: str = "pinned",
) -> InstallOutcome:
    """Returns a usable executable, preferring one the runner already provides.

    A preinstalled binary is taken on trust: it is whatever the runner image
    shipped, which is a supply chain this repository does not control and cannot
    meaningfully attest with a digest of its own choosing.
    """
    if (on_path := shutil.which(name)) is not None:
        return Installed(path=Path(on_path), version="preinstalled")
    if expected_digest is None:
        return InstallFailed(f"no pinned digest for this platform, and no {name} on PATH")
    return install(url, expected_digest, destination, encoding, version)
