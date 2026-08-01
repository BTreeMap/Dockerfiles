"""The derivation vocabulary: preconditions that used to be comments.

Each test here pins a rule that was previously restated beside a call site and
enforced at none of them. The valuable ones are the rejections: a scope too long
for personalisation, a part carrying the separator, a width outside BLAKE2b's
range. Those were all reachable, and all silent or deferred until first use.
"""

from __future__ import annotations

import hashlib

import pytest

from ci.derive import Derivation, Digest, Scope, material

SCOPE = Scope(b"test-v1")
OTHER = Scope(b"test-v2")
D = Derivation(scope=SCOPE, width=20)


# --- constructor invariants -------------------------------------------------


@pytest.mark.parametrize("label", [b"", b"x" * 17])
def test_a_scope_outside_the_personalisation_size_is_rejected(label: bytes) -> None:
    """Rejected at construction, which for a module constant is import time.

    BLAKE2b raises on an oversized `person` too, but only when the first digest
    is taken -- which in a build worker is minutes in, after the expensive part.
    """
    with pytest.raises(ValueError, match="a scope is"):
        Scope(label)


@pytest.mark.parametrize("width", [0, 65, -1])
def test_a_width_outside_the_digest_range_is_rejected(width: int) -> None:
    with pytest.raises(ValueError, match="a width is"):
        Derivation(scope=SCOPE, width=width)


def test_the_documented_limits_are_read_from_the_implementation() -> None:
    """The numbers the old comments carried, now sourced rather than restated."""
    assert Scope(b"x" * hashlib.blake2b.PERSON_SIZE)
    assert Derivation(scope=SCOPE, width=hashlib.blake2b.MAX_DIGEST_SIZE)


# --- the encoding -----------------------------------------------------------


def test_parts_cannot_run_together() -> None:
    """Injectivity, the property every call site used to assert in prose."""
    assert material("a", "b") != material("ab")
    assert material("1", "71") != material("17", "1")


def test_a_part_carrying_the_separator_is_rejected() -> None:
    """The case prose could not exclude, only hope about.

    Without this, a field that acquired a newline -- an unescaped `%0A` in a
    request path, say -- would let one input present another's exact message.
    """
    with pytest.raises(ValueError, match="may not contain"):
        material("GET", "/health\n2026", "0")


def test_the_offending_part_is_named() -> None:
    """A rejection that does not say which field is a bisect, not a diagnosis."""
    with pytest.raises(ValueError, match="bad\\\\nvalue"):
        material("fine", "bad\nvalue", "also fine")


# --- domain separation ------------------------------------------------------


def test_the_same_message_under_two_scopes_is_unrelated() -> None:
    """What personalisation buys, and the reason `Scope` is required.

    A derivation cannot be written without one, so this property holds for every
    digest in the repository rather than for the ones whose author remembered.
    """
    assert D.of("same") != Derivation(scope=OTHER, width=20).of("same")


def test_a_key_changes_the_digest_and_an_empty_key_is_the_unkeyed_mode() -> None:
    """`key=b""` is BLAKE2b's own unkeyed mode, not a sentinel for one.

    That is what lets one method serve both, so a MAC and a plain digest cannot
    drift apart in scope or width.
    """
    assert D.of("m", key=b"secret") != D.of("m")
    assert D.of("m", key=b"") == D.of("m")


def test_an_oversized_key_is_reduced_rather_than_rejected() -> None:
    """HMAC pre-hashes silently; BLAKE2b raises. The reduction is explicit."""
    assert len(D.of("m", key=b"x" * 5000).raw) == 20
    assert D.of("m", key=b"x" * 5000) != D.of("m", key=b"y" * 5000)


# --- projections ------------------------------------------------------------


def test_a_digest_renders_only_through_a_named_representation() -> None:
    digest = Digest(bytes(range(20)))

    assert digest.hex() == bytes(range(20)).hex()
    assert len(digest.base32()) == 32
    assert digest.integer() == int.from_bytes(bytes(range(20)), "big")


def test_base32_is_lowercase_and_unpadded_at_a_multiple_of_five_bits() -> None:
    rendered = D.of("anything").base32()

    assert rendered == rendered.lower()
    assert "=" not in rendered
    assert set(rendered) <= set("abcdefghijklmnopqrstuvwxyz234567")


def test_the_integer_projection_is_big_endian() -> None:
    """Byte order stated once here, so two seeds cannot disagree about it."""
    assert Digest(b"\x01\x00").integer() == 256
