"""Typed access to the environment, and writers for GitHub Actions channels.

The environment is untrusted input like any other, so every read passes through
one of these parsers rather than being coerced at the point of use.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class MissingEnvironment(RuntimeError):
    """A required variable is absent. A defect in the workflow, not a runtime case."""


def require(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise MissingEnvironment(f"Environment variable {name!r} is not set")
    return value


def optional(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or not value.strip() else value


def require_int(name: str, default: int | None = None) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        if default is None:
            raise MissingEnvironment(f"Environment variable {name!r} is not set")
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise MissingEnvironment(f"{name}={raw!r} is not an integer") from error


def require_json(name: str) -> Any:
    try:
        return json.loads(require(name))
    except json.JSONDecodeError as error:
        raise MissingEnvironment(f"{name} is not valid JSON: {error}") from error


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    """The timestamp and commit facts that every tag in a run is derived from.

    Read once and passed down rather than re-read per task, so every image in a
    run is guaranteed to carry the same date and commit.
    """

    date: str
    date_time: str
    commit_sha: str
    base_image: str

    @classmethod
    def from_environment(cls) -> BuildIdentity:
        registry = require("DOCKER_REGISTRY").lower()
        repository = require("DOCKER_IMAGE_NAME").lower()
        return cls(
            date=require("DATE_STR"),
            date_time=require("DATE_TIME_STR"),
            commit_sha=require("GITHUB_SHA"),
            base_image=f"{registry}/{repository}",
        )


def _append(channel: str, text: str) -> None:
    path = os.environ.get(channel)
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text)


def write_output(name: str, value: str) -> None:
    """Writes a step output, using heredoc form only when the value needs it."""
    body = (
        f"{name}<<__EOF__\n{value}\n__EOF__\n" if "\n" in value else f"{name}={value}\n"
    )
    _append("GITHUB_OUTPUT", body)


def write_summary(lines: Iterable[str]) -> None:
    _append("GITHUB_STEP_SUMMARY", "\n".join(lines) + "\n")


def mask(secret: str) -> None:
    """Registers a value for redaction before it can reach any log line."""
    print(f"::add-mask::{secret}", flush=True)
