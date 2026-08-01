"""Typed access to the environment, and writers for GitHub Actions channels.

The environment is untrusted input like any other, so every read passes through
one of these parsers rather than being coerced at the point of use.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from pydantic import BeforeValidator, Field, TypeAdapter, ValidationError

from ci.domain import BatchId


class MissingEnvironment(RuntimeError):
    """A required variable is absent. A defect in the workflow, not a runtime case."""


# --- the shapes a variable is allowed to have ------------------------------
#
# Named once, here, so a constraint is declared rather than re-checked at each
# call site. `require_int(name, default, minimum, maximum)` used to carry the
# bounds as arguments and enforce them with hand-written comparisons; that is
# `Field(ge=..., le=...)`, and writing it out by hand meant every caller had to
# remember which bound applied to it.

# Required and non-blank. A YAML expression that resolves to nothing renders as
# an empty string rather than an unset variable, so emptiness is absence here.
TEXT: TypeAdapter[str] = TypeAdapter(Annotated[str, Field(min_length=1)])

# May legitimately be empty: MESH_SECRET's absence disables stealing rather than
# failing the run, so "" is a value this one is allowed to carry.
OPTIONAL_TEXT: TypeAdapter[str] = TypeAdapter(str)

# A zero-based position in the build matrix.
INDEX: TypeAdapter[int] = TypeAdapter(Annotated[int, Field(ge=0)])

# A quantity of things that must exist: workers, build slots. `ge=1` is the
# whole reason BUILD_SLOTS=0 can no longer start zero threads, record zero
# outcomes, and exit green having built nothing.
COUNT: TypeAdapter[int] = TypeAdapter(Annotated[int, Field(ge=1)])

PORT: TypeAdapter[int] = TypeAdapter(Annotated[int, Field(ge=1, le=65535)])

# Unbounded on purpose: the workflow's convention is that a non-positive retry
# budget means unlimited, so `ge` would reject the very value that expresses it.
RETRIES: TypeAdapter[int] = TypeAdapter(int)

# Elements stay `Any` deliberately: the caller owns a smart constructor for them
# (`Task.parse`) and is the right place to reject one individually, so that a
# single malformed task does not discard the whole payload. What this
# establishes is the part that constructor cannot -- that iterating and counting
# the payload is meaningful at all. Handed a JSON object, the old code silently
# iterated its *keys*.
JSON_ARRAY: TypeAdapter[tuple[Any, ...]] = TypeAdapter(tuple[Any, ...])

# Stripped before the length is checked, so "   " is rejected rather than
# counted as three characters. `strict` keeps a JSON number out of a list of
# names: without it, `[1, 2]` would decode to `["1", "2"]` and an image called
# "1" would be looked for in the registry.
_Name = Annotated[
    str,
    BeforeValidator(lambda v: v.strip() if isinstance(v, str) else v),
    Field(strict=True, min_length=1),
]
NAME_LIST: TypeAdapter[tuple[str, ...]] = TypeAdapter(tuple[_Name, ...])


def generation_table() -> tuple[BatchId, ...]:
    """The batches the plan job pinned for this run, newest first.

    Empty is a valid answer and the bootstrap one: a registry with no labels yet
    yields no table, and every reference then floats exactly as it did before the
    mechanism existed. So absence is a default rather than a failure.

    Parsed, not trusted. The value crosses a job boundary as a string, and an
    entry that is not a batch id would otherwise be interpolated straight into a
    tag; `BatchId.parse` rejects it and the run degrades to floating instead.
    """
    raw = read("GENERATIONS", OPTIONAL_TEXT, default="")
    parsed = tuple(filter(None, map(BatchId.parse, filter(None, raw.split(",")))))
    if len(parsed) != len([part for part in raw.split(",") if part]):
        raise MissingEnvironment(f"GENERATIONS={raw!r}: not every entry is a batch id")
    return parsed


def _explain(name: str, source: object, error: ValidationError) -> str:
    """One line naming the variable, its value, and what was wrong with it.

    pydantic says what failed but not where it came from, and a bare "Input
    should be greater than or equal to 1" in a job log costs a bisect to place.
    Only the first problem is reported: these are scalars and short lists, so
    the first is almost always the whole story.
    """
    first = error.errors()[0]
    where = "".join(f"[{part!r}]" for part in first["loc"])
    return f"{name}{where}={source!r}: {first['msg']}"


def read[T](name: str, schema: TypeAdapter[T], default: T | None = None) -> T:
    """Reads one scalar variable, coerced and constrained by `schema`.

    The single reader that `require`, `optional`, and `require_int` used to be
    between them. Those three differed only in the type they produced and
    whether absence was fatal, which is exactly what a schema and a default
    already express.

    The default is validated by the same schema as the environment value rather
    than trusted and returned. A default outside its own range is a defect in
    this file, and the one place it must not be able to hide is the path taken
    when nobody has configured anything.
    """
    raw = os.environ.get(name)
    source: Any = raw if raw is not None and raw.strip() else default
    if source is None:
        raise MissingEnvironment(f"Environment variable {name!r} is not set")
    try:
        # Lax coercion, deliberately: everything arriving from the environment
        # is a string, so `BUILD_SLOTS=4` has to become the integer 4. Strictness
        # belongs on payload fields, where a JSON number really is a number.
        return schema.validate_python(source)
    except ValidationError as error:
        raise MissingEnvironment(_explain(name, source, error)) from error


def read_json[T](name: str, schema: TypeAdapter[T]) -> T:
    """Reads one variable whose contents are a JSON document.

    Separate from `read` because the encoding differs, not the intent: here the
    string is a document to be decoded, here it is the value itself. Collapsing
    the two would mean guessing which, and guessing wrong on `IMAGES=["a"]`
    yields the five-character string rather than a list.
    """
    raw = read(name, TEXT)
    try:
        return schema.validate_json(raw)
    except ValidationError as error:
        raise MissingEnvironment(_explain(name, raw, error)) from error


def registry_repository() -> str:
    """The registry reference this repository publishes every image under.

    Its own function again because two callers now need it and must agree
    exactly: `BuildIdentity` builds tags from it, and the plan job names the
    probe it walks the generation table through. A discrepancy between those two
    would have the table describing a different repository than the tags do.
    """
    registry = read("DOCKER_REGISTRY", TEXT).lower()
    repository = read("DOCKER_IMAGE_NAME", TEXT).lower()
    return f"{registry}/{repository}"


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    """The batch, timestamp, and commit facts that every tag in a run derives from.

    Read once and passed down rather than re-read per task, so every image in a
    run is guaranteed to carry the same batch, date, and commit.
    """

    date: str
    date_time: str
    commit_sha: str
    batch: BatchId
    base_image: str

    @classmethod
    def from_environment(cls) -> BuildIdentity:
        # Deliberately not a pydantic-settings model. That would aggregate these
        # seven failures into one report, which is worth something -- but none of
        # these names matches its field, the reads elsewhere are conditional
        # (MESH_SECRET) and dynamically defaulted (BUILD_SLOTS from cpu_count),
        # so the alias and factory boilerplate would exceed what it saves. The
        # constraint work is already pydantic's; only the lookup is not.
        # Bound before the call rather than read inside it: the batch is a
        # function of these two, and reading them twice would let the tag and the
        # token they appear in drift if a read ever became non-deterministic.
        date_time = read("DATE_TIME_STR", TEXT)
        commit_sha = read("GITHUB_SHA", TEXT)
        return cls(
            date=read("DATE_STR", TEXT),
            date_time=date_time,
            commit_sha=commit_sha,
            # PLAN_RUN_*, not GITHUB_RUN_*, and required rather than defaulted.
            # Both readings matter. The plan job pins these alongside the
            # timestamp so a partial re-run derives the batch its predecessor
            # published under; reading the runner's live variables instead would
            # give a re-run a batch of its own and hide every image already in
            # the registry from reconcile. And a default would let a job the
            # workflow forgot to thread fall back to "1" and derive a different
            # batch in silence -- the same failure, arrived at more quietly.
            batch=BatchId.derive(
                run_id=read("PLAN_RUN_ID", TEXT),
                run_attempt=read("PLAN_RUN_ATTEMPT", TEXT),
                commit_sha=commit_sha,
                date_time=date_time,
            ),
            base_image=registry_repository(),
        )


def _append(channel: str, text: str) -> None:
    path = os.environ.get(channel)
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(text)


def _assignment(name: str, value: str) -> str:
    """Renders one `name=value` record in the format the runner parses.

    Heredoc form only when the value needs it. A multi-line value written as a
    bare assignment does not fail -- it is silently truncated at the first
    newline, and the step that reads it gets a prefix it has no way to recognise
    as incomplete. One definition, because both channels share the format and a
    fix applied to one copy would leave the other quietly wrong.
    """
    if "\n" in value:
        return f"{name}<<__EOF__\n{value}\n__EOF__\n"
    return f"{name}={value}\n"


def write_output(name: str, value: str) -> None:
    """Writes a step output, readable by later steps through `needs`/`steps`."""
    _append("GITHUB_OUTPUT", _assignment(name, value))


def write_summary(lines: Iterable[str]) -> None:
    _append("GITHUB_STEP_SUMMARY", "\n".join(lines) + "\n")


def write_env(name: str, value: str) -> None:
    """Exports a variable to every *later* step in the job.

    Distinct from os.environ, which only reaches the current process. A value
    written here is how one step tells the next what it provisioned -- the
    machine-level equivalent of a return value.
    """
    _append("GITHUB_ENV", _assignment(name, value))


def mask(secret: str) -> None:
    """Registers a value for redaction before it can reach any log line."""
    print(f"::add-mask::{secret}", flush=True)
