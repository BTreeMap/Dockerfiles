"""The repository's own dependency graph, recovered from Dockerfile text.

The parser's job is to be right about which references point back at this
repository, and the tests that matter are the exclusions: a stage alias, a
scratch base, an external registry. Admitting one of those would put an image
nobody builds into the graph and fail discovery for a reference that was never
internal.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ci.domain import Dependency, Usage, selector_argument
from ci.references import (
    DanglingReference,
    External,
    Internal,
    Misdeclared,
    UnpinnableReference,
    classify,
    dependencies_in,
    dependents_of,
    graph,
    images_in_graph,
    logical_lines,
)

# The literal build argument every internal reference is written against, not a
# registry path: the parser recognises the form structurally, so no test here
# needs to know who publishes these images.
REGISTRY = "${REGISTRY}"


def parse(text: str) -> tuple[Dependency, ...]:
    return dependencies_in(text)


def ref(image: str) -> str:
    """A reference in the only form `graph` accepts: pinnable by its selector.

    Written as a helper rather than spelled out per fixture so these tests state
    the rule once. A fixture that hard-coded the argument name would keep passing
    if `selector_argument` changed and the Dockerfiles stopped matching it.
    """
    return f"{REGISTRY}:{image}${{{selector_argument(image)}}}"


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
    text = f"FROM {ref('code-server-base')}\nCOPY --from={ref('code-server-go')} /opt/go /opt/go\n"
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
        f"FROM {ref('code-server-base')} AS haskell_builder\n"
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
        f"FROM --platform=$BUILDPLATFORM {ref('code-server-base')} AS builder\n"
        f"COPY --chown=1000:1000 --from={ref('code-server-go')} /opt/go /opt/go\n"
    )
    assert {dependency.image for dependency in parse(text)} == {
        "code-server-base",
        "code-server-go",
    }


def test_one_image_may_be_both_a_base_and_a_source_of_artifacts() -> None:
    """Two distinct edges, so the pair is the unit of identity, not the name."""
    text = f"FROM {ref('a')}\nCOPY --from={ref('a')} /x /x\n"
    assert parse(text) == (
        Dependency(image="a", usage=Usage.ARTIFACT),
        Dependency(image="a", usage=Usage.BASE),
    )


def test_edges_are_deduplicated_and_ordered_independently_of_position() -> None:
    """Byte-stability, which is a digest property rather than a tidiness one.

    A label is part of the image configuration, so if moving a COPY within a file
    reordered this, the image's digest would change for an unchanged build.
    """
    first = f"COPY --from={ref('b')} /x /x\nCOPY --from={ref('a')} /y /y\n"
    second = f"COPY --from={ref('a')} /y /y\nCOPY --from={ref('b')} /x /x\n"
    repeated = second + f"COPY --from={ref('a')} /z /z\n"

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
    root = tree(tmp_path, {"app/Dockerfile": f"FROM {ref('typo-base')}\n"})

    with pytest.raises(DanglingReference) as raised:
        graph({"app": Path("app/Dockerfile")}, root)

    assert raised.value.dangling == {"typo-base": ("app",)}
    assert "typo-base" in str(raised.value)
    assert "app" in str(raised.value)


def test_the_graph_inverts_into_dependents(tmp_path: Path) -> None:
    root = tree(
        tmp_path,
        {
            "base/Dockerfile": "FROM scratch\n",
            "mid/Dockerfile": f"FROM {ref('base')}\n",
            "top/Dockerfile": f"FROM {ref('base')}\nCOPY --from={ref('mid')} /x /x\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "mid", "top")}

    edges = graph(definitions, root)
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
            "app/Dockerfile": f"FROM {ref('base')}\n",
            "alone/Dockerfile": "FROM alpine:3.21\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "app", "alone")}

    assert images_in_graph(graph(definitions, root)) == frozenset({"base", "app"})


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
            "code-server/Dockerfile": f"FROM {ref('code-server-base')}\n",
            # An unrelated family, added the way a future one would be.
            "warehouse/Dockerfile": "FROM debian:trixie\n",
            "warehouse/etl.Dockerfile": f"FROM {ref('warehouse')}\n",
            "reporting/Dockerfile": (
                f"FROM {ref('warehouse')}\nCOPY --from={ref('warehouse-etl')} /etl /etl\n"
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

    edges = graph(definitions, root)

    assert edges["reporting"] == (
        Dependency(image="warehouse", usage=Usage.BASE),
        Dependency(image="warehouse-etl", usage=Usage.ARTIFACT),
    )
    assert dependents_of(edges)["warehouse"] == ("reporting", "warehouse-etl")
    assert images_in_graph(edges) == frozenset(
        {"code-server-base", "code-server", "warehouse", "warehouse-etl", "reporting"}
    )


# --- pinnability ------------------------------------------------------------


def test_a_reference_without_a_selector_is_refused(tmp_path: Path) -> None:
    """The silent no-op this closes.

    Without the check the build passes a selector, the Dockerfile ignores it, the
    floating tag resolves to whatever is newest, and the label still reports the
    batch that was asked for. Every downstream reader is told something untrue,
    and nothing anywhere fails.
    """
    root = tree(
        tmp_path,
        {
            "base/Dockerfile": "FROM scratch\n",
            # Missing its selector: the whole point of this fixture.
            "app/Dockerfile": "FROM ${REGISTRY}:base\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "app")}

    with pytest.raises(UnpinnableReference) as raised:
        graph(definitions, root)

    assert "SELECT_BASE" in str(raised.value)


def test_a_reference_carrying_the_wrong_selector_is_refused(tmp_path: Path) -> None:
    """One image's selector must not be able to pin another's reference."""
    root = tree(
        tmp_path,
        {
            "base/Dockerfile": "FROM scratch\n",
            "app/Dockerfile": "FROM ${REGISTRY}:base${SELECT_SOMETHING_ELSE}\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "app")}

    with pytest.raises(UnpinnableReference, match="SELECT_BASE"):
        graph(definitions, root)


def test_two_images_sharing_one_selector_argument_are_refused(tmp_path: Path) -> None:
    """`selector_argument` folds punctuation, so it is many-to-one.

    `a-b` and `a.b` both become SELECT_A_B, and one would silently pin the other.
    Checked against the tree rather than assumed away, because the naming rules in
    ci/discovery.py can produce a dot in an image name.
    """
    root = tree(
        tmp_path, {"a-b/Dockerfile": "FROM scratch\n", "x/a.b.Dockerfile": "FROM scratch\n"}
    )
    definitions = {"a-b": Path("a-b/Dockerfile"), "a.b": Path("x/a.b.Dockerfile")}

    with pytest.raises(UnpinnableReference, match=r"\$\{SELECT_A_B\} is claimed by a-b, a\.b"):
        graph(definitions, root)


def test_an_external_reference_needs_no_selector(tmp_path: Path) -> None:
    """The rule binds only images we publish; nothing else is pinnable at all."""
    root = tree(tmp_path, {"app/Dockerfile": "FROM alpine:3.21\nCOPY --from=gcc:12 /a /b\n"})
    assert graph({"app": Path("app/Dockerfile")}, root) == {"app": ()}


def test_a_hardcoded_upstream_reference_is_caught_on_a_fork(tmp_path: Path) -> None:
    """The fork hazard, and why the check is keyed on the image name.

    A fork publishes under its own path. If it left `ghcr.io/upstream/...` in a
    Dockerfile and the check tested the registry, that reference would classify
    as external -- no graph, no defect, no labels -- and the fork would consume
    upstream's images while publishing its own. Keyed on the name, it is caught
    without this module knowing who upstream is.
    """
    root = tree(
        tmp_path,
        {
            "base/Dockerfile": "FROM scratch\n",
            "app/Dockerfile": "FROM ghcr.io/some-upstream/dockerfiles:base\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "app")}

    with pytest.raises(UnpinnableReference) as raised:
        graph(definitions, root)

    assert "${REGISTRY}:base${SELECT_BASE}" in str(raised.value)


def test_an_external_image_sharing_no_name_with_ours_is_untouched(tmp_path: Path) -> None:
    """The check binds only names this tree builds, so upstreams stay upstreams."""
    root = tree(
        tmp_path,
        {
            "app/Dockerfile": (
                "FROM lscr.io/linuxserver/code-server:latest\nFROM gcc:12.4.0-bookworm\n"
            )
        },
    )
    assert graph({"app": Path("app/Dockerfile")}, root) == {"app": ()}


# --- one classifier, three outcomes -----------------------------------------


def test_every_reference_lands_in_exactly_one_variant() -> None:
    """The sum that replaced asking "is this an edge" and "is this a defect" twice.

    Two traversals answering two questions over one regular expression could
    drift; one closed sum eliminated exhaustively cannot.
    """
    known = frozenset({"base"})

    assert classify(ref("base"), Usage.BASE, known) == Internal(
        Dependency(image="base", usage=Usage.BASE)
    )
    assert classify("alpine:3.21", Usage.BASE, known) == External()
    assert classify("builder_stage", Usage.ARTIFACT, known) == External()

    match classify("ghcr.io/upstream/dockerfiles:base", Usage.BASE, known):
        case Misdeclared(reference, complaint):
            assert reference == "ghcr.io/upstream/dockerfiles:base"
            assert "SELECT_BASE" in complaint
        case other:
            raise AssertionError(other)


def test_membership_is_what_separates_a_defect_from_an_external_image() -> None:
    """The same reference is a defect or not depending on what the tree builds.

    Which is the fork-safety property: nothing here knows a registry owner, only
    whether this repository publishes an image by that name.
    """
    reference = "ghcr.io/anyone/anything:widget"

    assert classify(reference, Usage.BASE, frozenset()) == External()
    assert isinstance(classify(reference, Usage.BASE, frozenset({"widget"})), Misdeclared)


def test_a_selector_argument_is_a_legal_dockerfile_argument_name() -> None:
    """`str.isalnum` is Unicode-aware and would have admitted `SELECT_CAFÉ_X`.

    A directory name is not restricted to ASCII, so an image name that is not
    either is reachable, and the argument derived from it has to stay nameable.
    """
    for image in ("code-server-base", "café-x", "a.b", "Ünicode"):
        argument = selector_argument(image)
        assert argument.isascii(), argument
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", argument), argument
