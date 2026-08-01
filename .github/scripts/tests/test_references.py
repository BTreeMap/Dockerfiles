"""The repository's own dependency graph, recovered from Dockerfile text.

The parser's job is to be right about which references point back at this
repository, and the tests that matter are the exclusions: a stage alias, a
scratch base, an external registry. Admitting one of those would put an image
nobody builds into the graph and fail discovery for a reference that was never
internal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ci.domain import Dependency, Usage
from ci.references import (
    DanglingReference,
    dependencies_in,
    dependents_of,
    graph,
    images_in_graph,
    logical_lines,
)

REGISTRY = "ghcr.io/btreemap/dockerfiles"


def parse(text: str) -> tuple[Dependency, ...]:
    return dependencies_in(text, REGISTRY)


# --- reading instructions ---------------------------------------------------


def test_continuations_are_joined_and_comments_dropped() -> None:
    text = "# a comment\nRUN one \\\n    two\n\nFROM scratch\n"
    assert tuple(logical_lines(text)) == ("RUN one two", "FROM scratch")


def test_a_comment_inside_a_continuation_is_still_a_comment() -> None:
    """Docker drops comment lines before joining, so the join must too."""
    assert tuple(logical_lines("RUN one \\\n# explanation\n    two\n")) == ("RUN one two",)


# --- what counts as internal ------------------------------------------------


def test_a_from_is_a_base_edge_and_a_copy_from_is_an_artifact_edge() -> None:
    """The distinction the whole mechanism exists to expose.

    Runtime libraries come from the base; binaries compiled elsewhere arrive over
    an artifact edge. A skew between the two is invisible if both are just "uses".
    """
    text = (
        f"FROM {REGISTRY}:code-server-base\nCOPY --from={REGISTRY}:code-server-go /opt/go /opt/go\n"
    )
    assert parse(text) == (
        Dependency(image="code-server-base", usage=Usage.BASE),
        Dependency(image="code-server-go", usage=Usage.ARTIFACT),
    )


def test_a_stage_alias_is_not_an_internal_reference() -> None:
    """The exclusion that needs no alias tracking to hold.

    A stage name cannot contain a slash or a colon, so it can never match the
    registry prefix. If this ever fails, the prefix test has been loosened into
    something that would put builder stages into the published graph.
    """
    text = (
        f"FROM {REGISTRY}:code-server-base AS haskell_builder\n"
        "FROM scratch\n"
        "COPY --from=haskell_builder /opt/haskell /opt/haskell\n"
    )
    assert parse(text) == (Dependency(image="code-server-base", usage=Usage.BASE),)


@pytest.mark.parametrize(
    "instruction",
    [
        "FROM scratch",
        "FROM lscr.io/linuxserver/code-server:latest",
        "FROM gcc:12.4.0-bookworm",
        "COPY --from=0 /a /b",
        "COPY /a /b",
        f"RUN echo {REGISTRY}:code-server-base",
        "FROM ghcr.io/someone-else/dockerfiles:code-server-base",
    ],
)
def test_an_external_or_local_reference_is_not_an_edge(instruction: str) -> None:
    assert parse(instruction + "\n") == ()


def test_flags_do_not_hide_the_operand() -> None:
    text = (
        f"FROM --platform=$BUILDPLATFORM {REGISTRY}:code-server-base AS builder\n"
        f"COPY --chown=1000:1000 --from={REGISTRY}:code-server-go /opt/go /opt/go\n"
    )
    assert {dependency.image for dependency in parse(text)} == {
        "code-server-base",
        "code-server-go",
    }


def test_one_image_may_be_both_a_base_and_a_source_of_artifacts() -> None:
    """Two distinct edges, so the pair is the unit of identity, not the name."""
    text = f"FROM {REGISTRY}:a\nCOPY --from={REGISTRY}:a /x /x\n"
    assert parse(text) == (
        Dependency(image="a", usage=Usage.ARTIFACT),
        Dependency(image="a", usage=Usage.BASE),
    )


def test_edges_are_deduplicated_and_ordered_independently_of_position() -> None:
    """Byte-stability, which is a digest property rather than a tidiness one.

    A label is part of the image configuration, so if moving a COPY within a file
    reordered this, the image's digest would change for an unchanged build.
    """
    first = f"COPY --from={REGISTRY}:b /x /x\nCOPY --from={REGISTRY}:a /y /y\n"
    second = f"COPY --from={REGISTRY}:a /y /y\nCOPY --from={REGISTRY}:b /x /x\n"
    repeated = second + f"COPY --from={REGISTRY}:a /z /z\n"

    assert parse(first) == parse(second) == parse(repeated)
    assert tuple(dependency.image for dependency in parse(first)) == ("a", "b")


# --- the graph over a tree --------------------------------------------------


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def test_a_reference_to_an_image_nothing_builds_is_refused(tmp_path: Path) -> None:
    """The silent hazard this turns loud.

    A misspelled or deleted internal image still resolves in the registry -- to
    whatever last published that tag, which could be arbitrarily old -- so the
    build succeeds and consumes something nobody intended, indefinitely.
    """
    root = tree(tmp_path, {"app/Dockerfile": f"FROM {REGISTRY}:typo-base\n"})

    with pytest.raises(DanglingReference) as raised:
        graph({"app": Path("app/Dockerfile")}, REGISTRY, root)

    assert raised.value.dangling == {"typo-base": ("app",)}
    assert "typo-base" in str(raised.value)
    assert "app" in str(raised.value)


def test_the_graph_inverts_into_dependents(tmp_path: Path) -> None:
    root = tree(
        tmp_path,
        {
            "base/Dockerfile": "FROM scratch\n",
            "mid/Dockerfile": f"FROM {REGISTRY}:base\n",
            "top/Dockerfile": f"FROM {REGISTRY}:base\nCOPY --from={REGISTRY}:mid /x /x\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "mid", "top")}

    edges = graph(definitions, REGISTRY, root)
    inverted = dependents_of(edges)

    assert inverted["base"] == ("mid", "top")
    assert inverted["mid"] == ("top",)
    assert inverted["top"] == ()


def test_graph_membership_covers_both_directions(tmp_path: Path) -> None:
    """The rule that decides which images carry provenance labels.

    A referenced image must be a member so its consumers can read a batch off it;
    a referencing image must be a member so it can be compared against what it
    consumed. An isolated image is in neither direction and keeps a digest that
    changes only when its content does.
    """
    root = tree(
        tmp_path,
        {
            "base/Dockerfile": "FROM scratch\n",
            "app/Dockerfile": f"FROM {REGISTRY}:base\n",
            "alone/Dockerfile": "FROM alpine:3.21\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "app", "alone")}

    assert images_in_graph(graph(definitions, REGISTRY, root)) == frozenset({"base", "app"})


def test_a_second_group_joins_the_graph_without_a_code_change(tmp_path: Path) -> None:
    """The generality requirement, demonstrated rather than asserted.

    A new family of interdependent images -- sharing no name, prefix, or
    directory with the existing one -- must produce edges, dependents, and
    membership on the strength of its Dockerfiles alone. Nothing in the parser
    knows any image of this repository, and this is what says so.
    """
    root = tree(
        tmp_path,
        {
            "code-server/base.Dockerfile": "FROM alpine:3.21\n",
            "code-server/Dockerfile": f"FROM {REGISTRY}:code-server-base\n",
            # An unrelated family, added the way a future one would be.
            "warehouse/Dockerfile": "FROM debian:trixie\n",
            "warehouse/etl.Dockerfile": f"FROM {REGISTRY}:warehouse\n",
            "reporting/Dockerfile": (
                f"FROM {REGISTRY}:warehouse\nCOPY --from={REGISTRY}:warehouse-etl /etl /etl\n"
            ),
        },
    )
    definitions = {
        "code-server-base": Path("code-server/base.Dockerfile"),
        "code-server": Path("code-server/Dockerfile"),
        "warehouse": Path("warehouse/Dockerfile"),
        "warehouse-etl": Path("warehouse/etl.Dockerfile"),
        "reporting": Path("reporting/Dockerfile"),
    }

    edges = graph(definitions, REGISTRY, root)

    assert edges["reporting"] == (
        Dependency(image="warehouse", usage=Usage.BASE),
        Dependency(image="warehouse-etl", usage=Usage.ARTIFACT),
    )
    assert dependents_of(edges)["warehouse"] == ("reporting", "warehouse-etl")
    assert images_in_graph(edges) == frozenset(
        {"code-server-base", "code-server", "warehouse", "warehouse-etl", "reporting"}
    )
