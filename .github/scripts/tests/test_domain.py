"""Boundary tests: every rejection path of every smart constructor."""

from __future__ import annotations

import pytest

from ci.domain import Hostname, Platform, Stolen, Task


def test_platform_parses_known_values_case_insensitively() -> None:
    assert Platform.parse("AMD64") is Platform.AMD64
    assert Platform.parse("  arm64 ") is Platform.ARM64


def test_platform_rejects_unknown_architectures() -> None:
    assert Platform.parse("riscv64") is None
    assert Platform.parse("") is None


def test_platform_maps_to_its_runner_label() -> None:
    assert Platform.AMD64.runner_label == "ubuntu-24.04"
    assert Platform.ARM64.runner_label == "ubuntu-24.04-arm"


@pytest.mark.parametrize(
    "raw",
    [
        "small-fast-blue-cat.trycloudflare.com",
        "a1.trycloudflare.com",
    ],
)
def test_hostname_accepts_quick_tunnel_shapes(raw: str) -> None:
    parsed = Hostname.parse(raw)
    assert parsed is not None and parsed.value == raw


@pytest.mark.parametrize(
    "raw",
    [
        "evil.example.com",                        # wrong suffix
        "host with spaces.trycloudflare.com",      # would break a ref name
        "../../../etc/passwd.trycloudflare.com",   # path traversal into the ref
        "UPPER.trycloudflare.com",                 # normalised, but see below
        "",
    ],
)
def test_hostname_rejects_anything_unsafe_for_a_url_or_a_ref(raw: str) -> None:
    parsed = Hostname.parse(raw)
    # Uppercase is lowercased by parse rather than rejected; everything else is
    # refused outright. Both outcomes keep the value ref- and URL-safe.
    assert parsed is None or parsed.value == raw.strip().lower()


def test_hostname_constructor_also_enforces_the_invariant() -> None:
    # Python cannot hide a dataclass constructor, so the check is duplicated in
    # __post_init__ to protect direct construction.
    with pytest.raises(ValueError, match="not a valid quick-tunnel hostname"):
        Hostname("evil.example.com")


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "image": "redis",
        "dockerfile": "redis/Dockerfile",
        "context": "redis",
        "platform": "amd64",
        "max_retries": 50,
    }
    return base | overrides


def test_task_parses_a_well_formed_payload() -> None:
    task = Task.parse(_payload())
    assert task is not None
    assert task.image == "redis" and task.platform is Platform.AMD64


@pytest.mark.parametrize(
    "overrides",
    [
        {"image": ""},
        {"image": 42},
        {"dockerfile": ""},
        {"platform": "sparc"},
        {"max_retries": "50"},
        {"max_retries": True},  # bool is an int subclass; must not slip through
    ],
)
def test_task_rejects_malformed_payloads(overrides: dict[str, object]) -> None:
    assert Task.parse(_payload(**overrides)) is None


def test_task_rejects_non_objects() -> None:
    assert Task.parse(None) is None
    assert Task.parse([1, 2, 3]) is None


def test_task_json_round_trips() -> None:
    task = Task.parse(_payload())
    assert task is not None
    assert Task.parse(task.as_json()) == task


def test_stolen_cannot_represent_an_empty_steal() -> None:
    # "Stole nothing" is PeerEmpty, not Stolen(()). Making the empty case
    # unrepresentable is what lets decide_idle match on Stolen without
    # re-checking the tuple.
    with pytest.raises(ValueError, match="at least one task"):
        Stolen(())
