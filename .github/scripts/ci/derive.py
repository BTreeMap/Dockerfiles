"""Scoped derivation: the one module that calls BLAKE2b.

Every digest this repository mints -- a mesh key, a request signature, a deal
seed, a batch id -- is the same primitive read at a different point in its index
space: a personalisation scope, an output width, and optionally a key. That
index used to be reassembled from keyword arguments at each call site, in three
modules, with the rule that held it together ("at most PERSON_SIZE bytes",
"these fields cannot contain the separator") written as a comment beside each
one. A rule stated four times is a rule nobody owns.

Here the index is a value. `Derivation` is a scope and a width; a derivation
that forgets its scope cannot be written, because `Scope` is a required field
rather than a keyword with a default. The preconditions BLAKE2b would raise on
are constructor invariants, so a malformed derivation fails at import -- before
a run has built anything -- rather than at its first use.
"""

from __future__ import annotations

import hashlib
from base64 import b32encode
from collections.abc import Sequence
from dataclasses import dataclass

# Read from the implementation rather than restated. The comments these replace
# carried the same numbers correctly and enforced none of them.
_PERSON_SIZE: int = hashlib.blake2b.PERSON_SIZE
_MAX_KEY_SIZE: int = hashlib.blake2b.MAX_KEY_SIZE
_MAX_DIGEST_SIZE: int = hashlib.blake2b.MAX_DIGEST_SIZE

# The separator that makes a sequence of parts one message. Newline because
# every part this repository derives from is a decimal number, a hex string, a
# punctuated timestamp, an HTTP method, or a path -- none of which may contain
# one, which `material` enforces rather than assumes.
_SEPARATOR = "\n"


@dataclass(frozen=True, slots=True)
class Scope:
    """What a derivation is *for*, mixed into the compression function.

    BLAKE2b personalisation gives domain separation natively: the same key over
    the same message under two scopes yields unrelated digests, because the
    scope changes the function rather than being prepended to the input and
    hoped for. That is why `Derivation` requires one. An unscoped digest here
    would be a value some other context could be persuaded to accept.
    """

    label: bytes

    def __post_init__(self) -> None:
        # In `__post_init__` rather than a factory because Python cannot make a
        # dataclass constructor private, so this is the only checkpoint every
        # route to a `Scope` must pass. Scopes are module constants, so an
        # oversized label is an import-time failure.
        if not 1 <= len(self.label) <= _PERSON_SIZE:
            raise ValueError(
                f"a scope is 1..{_PERSON_SIZE} bytes, not {len(self.label)}: {self.label!r}"
            )


@dataclass(frozen=True, slots=True)
class Digest:
    """A derived value, before a representation has been chosen.

    Separating derivation from rendering is what keeps a hex digest and a base32
    one from being compared: these projections are the only way out, and each
    names the alphabet it produces. Byte order and case folding are settled here
    once instead of at each call site, where two of them could disagree.
    """

    raw: bytes

    def hex(self) -> str:
        """For a wire value, hex being what the peers on the other end read."""
        return self.raw.hex()

    def base32(self) -> str:
        """For a tag component.

        Lowercased because a tag is case-sensitive while a repository name may
        not be, and a token that is sometimes shouted invites a 404 nobody can
        read. Base32's alphabet is A-Z and 2-7, which already excludes the 0/O
        and 1/I pairs, so folding the case costs no distinctness. A width that
        is a multiple of five bits encodes without padding; 20 bytes gives 32
        characters exactly.
        """
        return b32encode(self.raw).decode("ascii").lower()

    def integer(self) -> int:
        """For a seed. Big-endian, stated once so two seeds cannot disagree."""
        return int.from_bytes(self.raw, "big")


@dataclass(frozen=True, slots=True)
class Derivation:
    """One point in the BLAKE2b family: a scope and an output width.

    A value rather than a call, so what distinguishes this digest from every
    other digest in the repository is declared once beside its name.
    """

    scope: Scope
    width: int

    def __post_init__(self) -> None:
        if not 1 <= self.width <= _MAX_DIGEST_SIZE:
            raise ValueError(f"a width is 1..{_MAX_DIGEST_SIZE} bytes, not {self.width}")

    def of(self, *parts: str, key: bytes = b"") -> Digest:
        """Derives from structured parts, joined injectively by `material`."""
        return self.over(material(*parts), key=key)

    def over(self, message: bytes, key: bytes = b"") -> Digest:
        """Derives from an opaque message that is already bytes.

        Separate from `of` because the inputs differ in kind, not in encoding: a
        request body or a pre-rendered canonical form is one blob whose interior
        this module has no business splitting, while `of` is handed fields that
        must not be allowed to run together.

        An empty key is BLAKE2b's unkeyed mode exactly, not a sentinel standing
        in for one, which is why one method serves both and a keyed derivation
        needs no HMAC wrapper -- keyed BLAKE2b is already a MAC.
        """
        return Digest(
            hashlib.blake2b(
                message,
                key=_fit_key(key),
                person=self.scope.label,
                digest_size=self.width,
            ).digest()
        )


def material(*parts: str) -> bytes:
    """Encodes parts into one message, injectively.

    The check is the whole difference between an encoding and a concatenation.
    Without it `("a\\nb",)` and `("a", "b")` are the same message, and two
    distinct inputs sharing a digest is precisely what separating them is for.
    Each call site used to argue in a comment that its own fields could not
    contain the separator; the argument is made once here, and enforced, so the
    next caller inherits it instead of having to reproduce it.
    """
    offender = _containing_separator(parts)
    if offender is not None:
        raise ValueError(f"a derivation part may not contain {_SEPARATOR!r}: {offender!r}")
    return _SEPARATOR.join(parts).encode()


def _containing_separator(parts: Sequence[str]) -> str | None:
    """The first part that would break injectivity, or absence.

    `next` over a generator rather than a loop with a flag: it short-circuits on
    the first offender, and absence is a value the caller eliminates rather than
    a boolean it has to pair back up with the offending part.
    """
    return next((part for part in parts if _SEPARATOR in part), None)


def _fit_key(key: bytes) -> bytes:
    """Fits arbitrary key material into BLAKE2b's 64-byte key limit.

    HMAC pre-hashes an oversized key silently; BLAKE2b raises instead, so the
    reduction is explicit. An arbitrarily long secret stays valid -- it is
    compressed, exactly as HMAC would have done.

    The one BLAKE2b call here that carries no scope, and legitimately: this is
    not a digest any context consumes, it is a length reduction on the key the
    next call is about to take. There is nothing for a scope to separate it
    from.
    """
    if len(key) <= _MAX_KEY_SIZE:
        return key
    return hashlib.blake2b(key, digest_size=_MAX_KEY_SIZE).digest()
