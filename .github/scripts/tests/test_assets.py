"""The pinned-asset install: what may be executed, and what must never be.

Nothing here touches the network. The point of these tests is that the two
provisioning paths -- cloudflared's raw asset and mihomo's gzipped one -- now
share a single implementation, so the safety properties are asserted once and
hold for both rather than being written twice and drifting.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import stat
from pathlib import Path

import pytest

from ci import assets
from ci.assets import Encoding, digest_of, install, resolve
from ci.domain import Installed, InstallFailed

PAYLOAD = b"#!/bin/sh\necho hello\n"


class _FakeStream:
    """Stands in for `httpx.stream`, yielding bytes or raising on demand."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self, chunk_size: int = 0) -> list[bytes]:
        # Deliberately more than one chunk: a digest computed over a single
        # read would pass a test the streaming implementation must also pass.
        midpoint = len(self._body) // 2
        return [self._body[:midpoint], self._body[midpoint:]]


def _serve(monkeypatch: pytest.MonkeyPatch, body: bytes) -> list[str]:
    """Routes downloads to `body`, returning the list of URLs requested."""
    requested: list[str] = []

    def fake_stream(_method: str, url: str, **_kwargs: object) -> _FakeStream:
        requested.append(url)
        return _FakeStream(body)

    monkeypatch.setattr("ci.assets.httpx.stream", fake_stream)
    return requested


def _gzipped(body: bytes) -> bytes:
    return gzip.compress(body)


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


# --- the happy paths, one per encoding -------------------------------------


def test_a_raw_asset_is_installed_and_made_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(monkeypatch, PAYLOAD)
    destination = tmp_path / "cloudflared"

    outcome = install("https://example/bin", _digest(PAYLOAD), destination, Encoding.RAW)

    assert isinstance(outcome, Installed)
    assert destination.read_bytes() == PAYLOAD
    assert destination.stat().st_mode & stat.S_IXUSR


def test_a_gzipped_asset_is_verified_compressed_then_decompressed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The digest covers the bytes that arrive, not the bytes that result.

    Verifying before decompressing is what keeps a gzip decoder -- far more
    attack surface than a hash function -- from ever seeing unverified input.
    """
    compressed = _gzipped(PAYLOAD)
    _serve(monkeypatch, compressed)
    destination = tmp_path / "mihomo"

    outcome = install("https://example/bin.gz", _digest(compressed), destination, Encoding.GZIP)

    assert isinstance(outcome, Installed)
    assert destination.read_bytes() == PAYLOAD
    assert destination.stat().st_mode & stat.S_IXUSR


# --- rejection --------------------------------------------------------------


@pytest.mark.parametrize("encoding", [Encoding.RAW, Encoding.GZIP])
def test_a_substituted_asset_is_never_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, encoding: Encoding
) -> None:
    """A digest mismatch must leave nothing at all behind to be executed."""
    _serve(monkeypatch, b"malicious payload")
    destination = tmp_path / "binary"

    outcome = install("https://example/bin", _digest(PAYLOAD), destination, encoding)

    assert isinstance(outcome, InstallFailed)
    assert "digest mismatch" in outcome.reason
    assert not destination.exists()
    # And no staging debris for a later run to trip over.
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_leaves_nothing_half_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def exploding_stream(_method: str, _url: str, **_kwargs: object) -> _FakeStream:
        raise OSError("connection reset")

    monkeypatch.setattr("ci.assets.httpx.stream", exploding_stream)
    destination = tmp_path / "binary"

    outcome = install("https://example/bin", _digest(PAYLOAD), destination)

    assert isinstance(outcome, InstallFailed)
    assert "download failed" in outcome.reason
    assert list(tmp_path.iterdir()) == []


def test_corrupt_gzip_is_reported_rather_than_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bytes whose digest is right but whose gzip framing is not."""
    body = b"not actually gzip"
    _serve(monkeypatch, body)
    destination = tmp_path / "binary"

    outcome = install("https://example/bin.gz", _digest(body), destination, Encoding.GZIP)

    assert isinstance(outcome, InstallFailed)
    assert "decompression failed" in outcome.reason
    assert not destination.exists()


# --- reuse: the asymmetry this module was written to remove -----------------


def test_a_binary_from_this_exact_asset_is_reused_without_downloading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    requested = _serve(monkeypatch, PAYLOAD)
    destination = tmp_path / "binary"

    assert isinstance(install("https://e/bin", _digest(PAYLOAD), destination), Installed)
    assert len(requested) == 1

    # Second call: the stamp proves provenance, so nothing is fetched again.
    assert isinstance(install("https://e/bin", _digest(PAYLOAD), destination), Installed)
    assert len(requested) == 1


def test_a_cached_binary_of_unknown_provenance_is_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bug this module exists to close.

    The compressed path used to reuse whatever happened to sit at the
    destination, checking only that the file existed -- so a tampered cache was
    re-verified for cloudflared and executed unread for mihomo, under a docstring
    claiming neither could happen. Provenance is now required for both.
    """
    tampered = b"tampered binary that was never verified"
    destination = tmp_path / "binary"
    destination.write_bytes(tampered)

    compressed = _gzipped(PAYLOAD)
    requested = _serve(monkeypatch, compressed)
    outcome = install("https://e/bin", _digest(compressed), destination, Encoding.GZIP)

    # It refused to trust an unstamped file, fetched the pinned asset, and
    # overwrote the impostor with content whose digest was proven.
    assert len(requested) == 1
    assert isinstance(outcome, Installed)
    assert destination.read_bytes() == PAYLOAD


def test_a_rejected_asset_never_overwrites_with_unverified_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed install must not be a way to write the destination.

    The pre-existing file is left exactly as it was -- this function does not
    own it -- and, more importantly, the caller is handed `InstallFailed` and so
    never executes the path at all.
    """
    existing = b"whatever was already here"
    destination = tmp_path / "binary"
    destination.write_bytes(existing)

    _serve(monkeypatch, b"malicious payload")
    outcome = install("https://e/bin", _digest(PAYLOAD), destination)

    assert isinstance(outcome, InstallFailed)
    assert destination.read_bytes() == existing


def test_a_binary_stamped_for_a_different_version_is_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "binary"
    requested = _serve(monkeypatch, PAYLOAD)
    install("https://e/bin", _digest(PAYLOAD), destination)
    assert len(requested) == 1

    # Same path, different pinned digest: the stamp no longer vouches for it.
    updated = b"#!/bin/sh\necho goodbye\n"
    requested_again = _serve(monkeypatch, updated)
    outcome = install("https://e/bin", _digest(updated), destination)

    assert isinstance(outcome, Installed)
    assert len(requested_again) == 1
    assert destination.read_bytes() == updated


# --- resolve ----------------------------------------------------------------


def test_a_preinstalled_binary_on_path_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("ci.assets.shutil.which", lambda _name: "/usr/bin/cloudflared")
    outcome = resolve("cloudflared", "https://e/bin", _digest(PAYLOAD), tmp_path / "x")
    assert outcome == Installed(path=Path("/usr/bin/cloudflared"), version="preinstalled")


def test_an_unsupported_platform_degrades_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No pinned digest means no download: an unverifiable binary is not run."""
    monkeypatch.setattr("ci.assets.shutil.which", lambda _name: None)
    outcome = resolve("mihomo", "https://e/bin", None, tmp_path / "x")
    assert isinstance(outcome, InstallFailed)
    assert "no pinned digest" in outcome.reason


# --- the digest helper ------------------------------------------------------


def test_digest_streams_a_file_larger_than_one_chunk(tmp_path: Path) -> None:
    body = os.urandom(assets._CHUNK_BYTES + 1024)
    path = tmp_path / "large"
    path.write_bytes(body)
    assert digest_of(path) == hashlib.sha256(body).hexdigest()
