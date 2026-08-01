"""The environment boundary: where untrusted strings become typed values.

Every variable read here comes from workflow YAML or a previous job's output, so
none of it is any more trustworthy than a network payload. These tests pin the
rejections -- particularly the ones whose absence produced a *green* run rather
than a red one, which are the expensive kind to discover later.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ci.domain import BatchId
from ci.env import (
    COUNT,
    JSON_ARRAY,
    NAME_LIST,
    OPTIONAL_TEXT,
    PORT,
    TEXT,
    BuildIdentity,
    MissingEnvironment,
    generation_table,
    read,
    read_json,
    write_env,
    write_output,
    write_summary,
)

# --- scalars ----------------------------------------------------------------


def test_require_rejects_absent_and_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_VAR", raising=False)
    with pytest.raises(MissingEnvironment):
        read("SOME_VAR", TEXT)

    # Whitespace is absence: a YAML expression that resolved to nothing renders
    # as an empty string, not as an unset variable.
    monkeypatch.setenv("SOME_VAR", "   ")
    with pytest.raises(MissingEnvironment):
        read("SOME_VAR", TEXT)


def test_optional_falls_back_on_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_VAR", "  ")
    assert read("SOME_VAR", OPTIONAL_TEXT, default="fallback") == "fallback"
    monkeypatch.setenv("SOME_VAR", "set")
    assert read("SOME_VAR", OPTIONAL_TEXT, default="fallback") == "set"


def test_a_non_integer_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COUNT", "four")
    with pytest.raises(MissingEnvironment, match="valid integer"):
        read("COUNT", COUNT)


def test_the_default_is_used_only_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COUNT", raising=False)
    assert read("COUNT", COUNT, default=4) == 4
    monkeypatch.setenv("COUNT", "9")
    assert read("COUNT", COUNT, default=4) == 9


def test_zero_build_slots_is_rejected_rather_than_silently_building_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The expensive one.

    `BUILD_SLOTS=0` used to parse cleanly, start zero threads, record zero
    outcomes and exit 0 -- a run that reported success having built nothing.
    A misconfiguration must fail loudly, not produce a green tick.
    """
    monkeypatch.setenv("BUILD_SLOTS", "0")
    with pytest.raises(MissingEnvironment, match="greater than or equal to 1"):
        read("BUILD_SLOTS", COUNT, default=4)


def test_bounds_are_applied_to_the_default_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A default outside its own range is a defect, not a value to hand back."""
    monkeypatch.delenv("PORT", raising=False)
    with pytest.raises(MissingEnvironment, match="less than or equal to 65535"):
        read("PORT", PORT, default=99999)


def test_a_port_above_the_range_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(MissingEnvironment, match="less than or equal to 65535"):
        read("PORT", PORT, default=1080)


# --- structured input -------------------------------------------------------


def test_a_json_object_is_rejected_where_an_array_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JSON object is iterable, which is exactly why it has to be rejected.

    The old reader handed the decoded value straight to `map`, so an object
    silently iterated its *keys* and produced a plausible-looking task list.
    """
    monkeypatch.setenv("WORKER_TASKS", json.dumps({"image": "redis"}))
    with pytest.raises(MissingEnvironment, match="WORKER_TASKS"):
        read_json("WORKER_TASKS", JSON_ARRAY)


def test_malformed_json_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_TASKS", "{not json")
    with pytest.raises(MissingEnvironment, match="WORKER_TASKS"):
        read_json("WORKER_TASKS", JSON_ARRAY)


def test_array_elements_are_kept_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Element validation belongs to Task.parse, which can reject one at a time."""
    monkeypatch.setenv("WORKER_TASKS", json.dumps([{"a": 1}, "junk", 3]))
    assert read_json("WORKER_TASKS", JSON_ARRAY) == ({"a": 1}, "junk", 3)


def test_a_name_list_is_stripped_and_returned_as_a_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IMAGES", json.dumps(["  redis ", "nginx"]))
    assert read_json("IMAGES", NAME_LIST) == ("redis", "nginx")


@pytest.mark.parametrize(
    "payload",
    [
        ["redis", ""],       # empty name
        ["redis", "   "],    # blank after stripping, not merely non-empty
        ["redis", None],
        ["redis", 1],        # would otherwise coerce to the image name "1"
        {"redis": True},     # an object, not a list
    ],
)
def test_a_name_list_rejects_anything_but_names(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    monkeypatch.setenv("IMAGES", json.dumps(payload))
    with pytest.raises(MissingEnvironment, match="IMAGES"):
        read_json("IMAGES", NAME_LIST)


def test_an_empty_list_is_a_valid_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emptiness is the caller's policy, not the decoder's."""
    monkeypatch.setenv("IMAGES", "[]")
    assert read_json("IMAGES", NAME_LIST) == ()


# --- output channels --------------------------------------------------------


def test_a_single_line_output_is_written_as_a_plain_assignment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(path))
    write_output("images", '["redis"]')
    assert path.read_text() == 'images=["redis"]\n'


def test_a_multi_line_value_uses_heredoc_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without this the runner truncates at the first newline, silently.

    The reader then gets a prefix it has no way to recognise as incomplete,
    which is a far worse failure than a parse error would have been.
    """
    path = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(path))
    write_output("matrix", "line one\nline two")
    assert path.read_text() == "matrix<<__EOF__\nline one\nline two\n__EOF__\n"


def test_env_and_output_share_one_format(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out, env = tmp_path / "out", tmp_path / "env"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_ENV", str(env))
    write_output("k", "a\nb")
    write_env("k", "a\nb")
    assert out.read_text() == env.read_text()


def test_writing_to_an_unconfigured_channel_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outside Actions these variables are unset; the scripts must still run."""
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    write_output("k", "v")
    write_summary(["a line"])


def test_summary_lines_are_joined_and_terminated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    write_summary(["### Worker 1", "", "- ok"])
    assert path.read_text() == "### Worker 1\n\n- ok\n"


def test_appending_preserves_earlier_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Several steps write the same file; one must never truncate another's."""
    path = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(path))
    write_output("first", "1")
    write_output("second", "2")
    assert path.read_text() == "first=1\nsecond=2\n"


# --- the run identity -------------------------------------------------------


def _stage_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The variables every one of the three stages is given by the plan job."""
    for name, value in {
        "DOCKER_REGISTRY": "GHCR.IO",
        "DOCKER_IMAGE_NAME": "BTreeMap/Dockerfiles",
        "DATE_STR": "2026-07-28",
        "DATE_TIME_STR": "2026-07-28.12-00-00",
        "GITHUB_SHA": "abc123",
        "PLAN_RUN_ID": "18234567891",
        "PLAN_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(name, value)


def test_every_stage_of_a_run_derives_the_same_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The property the batch id exists to provide.

    Build, reconcile, and manifest each construct their own identity from the
    environment rather than being handed one. If those disagreed, reconcile would
    find no evidence of any build and the manifest stage would fuse nothing.
    """
    _stage_environment(monkeypatch)
    assert BuildIdentity.from_environment() == BuildIdentity.from_environment()


def test_a_partial_re_run_stays_in_the_batch_it_is_repairing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case that makes these values plan-pinned rather than read live.

    "Re-run failed jobs" leaves the plan job alone and replays its outputs, so a
    re-run of the build stage is handed attempt 1's values while the runner's own
    GITHUB_RUN_ATTEMPT says 2. The batch must follow the plan: the images that
    already landed were published under attempt 1's, and reconcile finds them by
    that name or not at all.
    """
    _stage_environment(monkeypatch)
    planned = BuildIdentity.from_environment()

    # What the runner reports during the re-run. Nothing here may read it.
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_RUN_ID", "99999999999")
    assert BuildIdentity.from_environment().batch == planned.batch


def test_a_fresh_plan_gets_its_own_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running the plan job re-runs everything downstream, so a new batch is
    the correct outcome -- and the attempt is what distinguishes it, since the
    run id is stable across re-runs of one run."""
    _stage_environment(monkeypatch)
    first = BuildIdentity.from_environment()

    monkeypatch.setenv("PLAN_RUN_ATTEMPT", "2")
    assert BuildIdentity.from_environment().batch != first.batch


@pytest.mark.parametrize("missing", ["PLAN_RUN_ID", "PLAN_RUN_ATTEMPT"])
def test_an_unthreaded_plan_value_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """Required, not defaulted.

    A default would let a job the workflow forgot to thread derive a different
    batch in silence, which is the same corruption as reading the live attempt
    but harder to notice.
    """
    _stage_environment(monkeypatch)
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(MissingEnvironment, match=missing):
        BuildIdentity.from_environment()


def test_the_registry_reference_is_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository name may not carry uppercase; the workflow supplies both cased."""
    _stage_environment(monkeypatch)
    assert BuildIdentity.from_environment().base_image == "ghcr.io/btreemap/dockerfiles"


# --- the generation table ---------------------------------------------------


def test_no_table_is_the_bootstrap_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A registry with no labels yields nothing, and every edge then floats.

    Absence has to be a default rather than a failure, or the very first run
    against an empty registry could never produce the labels the table is read
    from.
    """
    monkeypatch.delenv("GENERATIONS", raising=False)
    assert generation_table() == ()
    monkeypatch.setenv("GENERATIONS", "")
    assert generation_table() == ()


def test_the_table_is_parsed_not_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """It crosses a job boundary as a string and is interpolated into a tag.

    An entry that is not a batch id would otherwise be pasted straight into a
    reference, so it is refused here rather than resolved into a 404 much later.
    """
    monkeypatch.setenv("GENERATIONS", "not-a-batch")
    with pytest.raises(MissingEnvironment, match="not every entry is a batch id"):
        generation_table()


def test_the_table_keeps_its_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Newest first: an edge reaching back k takes the k-th entry."""
    first = BatchId.derive(run_id="1", run_attempt="1", commit_sha="a", date_time="t")
    second = BatchId.derive(run_id="2", run_attempt="1", commit_sha="a", date_time="t")
    monkeypatch.setenv("GENERATIONS", f"{first},{second}")
    assert generation_table() == (first, second)
