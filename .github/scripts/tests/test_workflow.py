"""The contract between the workflow and the scripts it runs.

A script reads its inputs from the environment, and the workflow decides what is
in it. Nothing checked that those two agreed, so a job could be handed two of the
six variables its first statement needs and fail three minutes into a run, after
discovery had already done its work. These tests are that check.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from ci.env import BuildIdentity

WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/docker-publish.yml"


def _workflow_env() -> frozenset[str]:
    """Names declared by the workflow's top-level `env:` block."""
    text = WORKFLOW.read_text()
    block = text[text.index("\nenv:\n") + 1 :]
    block = block[: block.index("\njobs:")]
    return frozenset(re.findall(r"^  ([A-Z_][A-Z0-9_]*):", block, re.MULTILINE))


def step_env(step: str) -> frozenset[str]:
    """Names one named step declares, plus the workflow-level ones it inherits."""
    text = WORKFLOW.read_text()
    after = text[text.index(f"- name: {step}") :]
    following = re.search(r"\n      - name: ", after)
    within = after[: following.start()] if following else after
    declared = re.findall(r"^          ([A-Z_][A-Z0-9_]*):", within, re.MULTILINE)
    return frozenset(declared) | _workflow_env()


@pytest.mark.parametrize(
    "step",
    [
        "Discover and deal build tasks",
        "Build and push assigned images, stealing when idle",
        "Verify every expected image landed, rebuild what did not",
        "Create and Push Docker Manifests",
    ],
)
def test_every_stage_can_build_its_identity(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    """Each stage that derives the run identity is given every input it takes.

    The regression this exists for: the plan job derived the batch from six
    variables and was handed two, which surfaced only in CI, only after discovery
    had finished, and only because the summary happened to want it.

    Exercised by constructing the identity against exactly what the workflow
    declares, rather than by comparing two lists that could both be wrong.
    """
    monkeypatch.setattr(os, "environ", {name: "x" for name in step_env(step)})
    assert BuildIdentity.from_environment()


def test_the_step_names_this_file_asserts_on_still_exist() -> None:
    """Guards the guard: a renamed step would make every case above vacuous."""
    text = WORKFLOW.read_text()
    for step in (
        "Discover and deal build tasks",
        "Build and push assigned images, stealing when idle",
        "Verify every expected image landed, rebuild what did not",
        "Create and Push Docker Manifests",
    ):
        assert f"- name: {step}" in text, step
