"""The repository's own dependency graph, recovered from Dockerfile text.

The parser's job is to be right about which references point back at this
repository, and the tests that matter are the exclusions: a stage alias, a
scratch base, an external registry. Admitting one of those would put an image
nobody builds into the graph and fail discovery for a reference that was never
internal.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from ci.domain import Dependency, Usage
from ci.references import (
    CyclicGraph,
    DanglingReference,
    External,
    Internal,
    Misdeclared,
    UnpinnableReference,
    classify,
    dependencies_in,
    dependents_of,
    generations_needed,
    graph,
    images_in_graph,
    logical_lines,
    probe_for,
)

# Any registry will do. The parser keys on the image name a declaration's default
# ends with, never on who publishes it, which is what keeps a fork's checkout
# readable without edits.
REGISTRY = "ghcr.io/example/dockerfiles"


# Every image any fixture here references. Declaring extras is inert: a
# declaration whose default names an image the tree does not build is ignored,
# because internality is decided by the image name and nothing else.
FIXTURE_IMAGES = (
    "a",
    "b",
    "base",
    "mid",
    "top",
    "typo-base",
    "code-server",
    "code-server-base",
    "code-server-go",
    "warehouse",
    "warehouse-etl",
)

KNOWN = frozenset(FIXTURE_IMAGES)


def preamble(images: Iterable[str]) -> str:
    """The declarations a fixture's references resolve through.

    Supplied by the helpers rather than written into each fixture, so these tests
    stay about references rather than about boilerplate.
    """
    return "".join(declare(image) for image in images)


def parse(text: str) -> tuple[Dependency, ...]:
    return dependencies_in(preamble(FIXTURE_IMAGES) + text, KNOWN)


def declare(image: str, argument: str | None = None) -> str:
    """A declaration plus the reference through it, the only form `graph` accepts.

    The argument name defaults to something unrelated to the image, which is the
    point: nothing connects the two but the declaration itself.
    """
    return f"ARG {argument or _argument(image)}={REGISTRY}:{image}\n"


def _argument(image: str) -> str:
    return "REF_" + image.upper().replace("-", "_").replace(".", "_")


def ref(image: str, argument: str | None = None) -> str:
    """The reference itself, which must be declared above wherever it appears."""
    return "${" + (argument or _argument(image)) + "}"


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
        Dependency(image="code-server-base", usage=Usage.BASE, argument="REF_CODE_SERVER_BASE"),
        Dependency(image="code-server-go", usage=Usage.ARTIFACT, argument="REF_CODE_SERVER_GO"),
    )


def test_usage_follows_a_stage_position_not_its_instruction() -> None:
    """The distinction the instruction alone can no longer make.

    BuildKit will not expand an argument inside `COPY --from`, so a consumer names
    the images it copies with FROM, exactly as a toolchain names the base it
    compiles on. Only position relative to the published stage tells them apart:
    what the last FROM descends from is a base, and everything else is a source of
    artifacts.

    Here the file publishes `FROM scratch`, so the image it compiled on is not a
    base of anything it ships; its contents merely expect that base's libraries,
    which is what an artifact edge records.
    """
    text = (
        f"FROM {ref('code-server-base')} AS haskell_builder\n"
        "FROM scratch\n"
        "COPY --from=haskell_builder /opt/haskell /opt/haskell\n"
    )
    assert parse(text) == (
        Dependency(image="code-server-base", usage=Usage.ARTIFACT, argument="REF_CODE_SERVER_BASE"),
    )


def test_the_published_stage_decides_which_reference_is_the_base() -> None:
    """The consumer shape: hoisted stages are artifacts, the final FROM is the base."""
    text = (
        f"FROM {ref('code-server-go')} AS go_artifacts\n"
        f"FROM {ref('code-server-base')}\n"
        "COPY --from=go_artifacts /opt/go /opt/go\n"
    )
    assert parse(text) == (
        Dependency(image="code-server-base", usage=Usage.BASE, argument="REF_CODE_SERVER_BASE"),
        Dependency(image="code-server-go", usage=Usage.ARTIFACT, argument="REF_CODE_SERVER_GO"),
    )


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
        Dependency(image="a", usage=Usage.ARTIFACT, argument="REF_A"),
        Dependency(image="a", usage=Usage.BASE, argument="REF_A"),
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


def image_of(path: str) -> str:
    """The image name discovery would give this path, mirroring ci/discovery."""
    directory, _, filename = path.rpartition("/")
    stem = filename.removesuffix(".Dockerfile")
    return directory if filename == "Dockerfile" else f"{directory}-{stem}"


def tree(tmp_path: Path, files: dict[str, str], also: Iterable[str] = ()) -> Path:
    """Writes a tree, declaring the images it defines above every file.

    Declaring only what the tree builds keeps each fixture honest: a declaration
    for an image nothing defines is precisely the dangling reference, so it has to
    be asked for explicitly rather than supplied by the helper.
    """
    declared = preamble([*sorted(map(image_of, files)), *also])
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(declared + text)
    return tmp_path


def test_a_reference_to_an_image_nothing_builds_is_refused(tmp_path: Path) -> None:
    """The silent hazard this turns loud.

    A misspelled or deleted internal image still resolves in the registry -- to
    whatever last published that tag, which could be arbitrarily old -- so the
    build succeeds and consumes something nobody intended, indefinitely.
    """
    root = tree(
        tmp_path,
        {"base/Dockerfile": "FROM scratch\n", "app/Dockerfile": f"FROM {ref('typo-base')}\n"},
        also=("typo-base",),
    )

    with pytest.raises(DanglingReference) as raised:
        graph({name: Path(f"{name}/Dockerfile") for name in ("base", "app")}, root)

    assert raised.value.dangling == {"typo-base": ("app", "base")}
    assert "typo-base" in str(raised.value)


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

    # The depth rule falls out of the new group's own shape, with nothing here
    # naming it: `reporting` sits two levels above `warehouse` and one above
    # `warehouse-etl`, so the base it lands on must reach a generation further
    # back than the artifacts compiled against that base.
    assert edges["reporting"] == (
        Dependency(
            image="warehouse",
            usage=Usage.BASE,
            argument="REF_WAREHOUSE",
            generations_back=2,
        ),
        Dependency(
            image="warehouse-etl",
            usage=Usage.ARTIFACT,
            argument="REF_WAREHOUSE_ETL",
            generations_back=1,
        ),
    )
    assert dependents_of(edges)["warehouse"] == ("reporting", "warehouse-etl")
    assert images_in_graph(edges) == frozenset(
        {"code-server-base", "code-server", "warehouse", "warehouse-etl", "reporting"}
    )


# --- pinnability ------------------------------------------------------------


def test_a_literal_reference_to_one_of_our_images_is_refused(tmp_path: Path) -> None:
    """The silent no-op this closes.

    Without the check the build passes an argument, the Dockerfile ignores it, the
    floating tag resolves to whatever is newest, and the label still reports the
    batch that was asked for. Every downstream reader is told something untrue,
    and nothing anywhere fails.

    Keyed on the image name rather than the registry path, which is what catches a
    fork that never updated its Dockerfiles: the reference is refused whoever owns
    the path it points at.
    """
    root = tree(
        tmp_path,
        {
            "base/Dockerfile": "FROM scratch\n",
            "app/Dockerfile": f"FROM {REGISTRY}:base\n",
            "forked/Dockerfile": "FROM ghcr.io/some-upstream/dockerfiles:base\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "app", "forked")}

    with pytest.raises(UnpinnableReference) as raised:
        graph(definitions, root)

    assert set(raised.value.defects) == {"app/Dockerfile", "forked/Dockerfile"}
    assert "written literally" in str(raised.value)
    assert "ARG <NAME>=" in str(raised.value)


def test_an_argument_redeclared_with_a_different_default_is_refused(tmp_path: Path) -> None:
    """The only way free argument names can go wrong, refused at the cause.

    The build sets an argument once, so a file whose references resolve it two
    ways would have one of them silently redirected. Refusing the redeclaration
    is stricter than detecting the redirect and needs no reasoning about which
    reference won.
    """
    root = tree(
        tmp_path,
        {
            "a/Dockerfile": "FROM scratch\n",
            "b/Dockerfile": "FROM scratch\n",
            "app/Dockerfile": (
                f"ARG SHARED={REGISTRY}:a\n"
                "FROM ${SHARED}\n"
                f"ARG SHARED={REGISTRY}:b\n"
                "COPY --from=${SHARED} /x /x\n"
            ),
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("a", "b", "app")}

    with pytest.raises(UnpinnableReference, match="redeclared with a different default"):
        graph(definitions, root)


def test_an_external_reference_needs_no_declaration(tmp_path: Path) -> None:
    """The rule binds only images we publish; nothing else is pinnable at all."""
    root = tree(tmp_path, {"app/Dockerfile": "FROM alpine:3.21\nCOPY --from=gcc:12 /a /b\n"})
    assert graph({"app": Path("app/Dockerfile")}, root) == {"app": ()}


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
    bindings = {"REF_BASE": f"{REGISTRY}:base"}

    assert classify(ref("base"), Usage.BASE, bindings, known) == Internal(
        Dependency(image="base", usage=Usage.BASE, argument="REF_BASE")
    )
    assert classify("alpine:3.21", Usage.BASE, bindings, known) == External()
    assert classify("builder_stage", Usage.ARTIFACT, bindings, known) == External()

    match classify("ghcr.io/upstream/dockerfiles:base", Usage.BASE, bindings, known):
        case Misdeclared(reference, complaint):
            assert reference == "ghcr.io/upstream/dockerfiles:base"
            assert "written literally" in complaint
        case other:
            raise AssertionError(other)


def test_membership_is_what_separates_a_defect_from_an_external_image() -> None:
    """The same reference is a defect or not depending on what the tree builds.

    Which is the fork-safety property: nothing here knows a registry owner, only
    whether this repository publishes an image by that name.
    """
    reference = "ghcr.io/anyone/anything:widget"

    assert classify(reference, Usage.BASE, {}, frozenset()) == External()
    assert isinstance(classify(reference, Usage.BASE, {}, frozenset({"widget"})), Misdeclared)


def test_a_cycle_is_refused_rather_than_walked(tmp_path: Path) -> None:
    """A level is one more than the deepest thing below it, which a cycle leaves
    undefined -- and the walk that computes it would not terminate."""
    root = tree(
        tmp_path,
        {"a/Dockerfile": f"FROM {ref('b')}\n", "b/Dockerfile": f"FROM {ref('a')}\n"},
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("a", "b")}

    with pytest.raises(CyclicGraph, match="->"):
        graph(definitions, root)


def test_the_probe_is_an_image_one_generation_above_a_root(tmp_path: Path) -> None:
    """What the generation walk steps through, chosen by rule not by name.

    Any such image reports the same generation -- floating tags advance only as a
    complete set -- so the choice is alphabetical solely to keep a run
    reproducible from its inputs.
    """
    root = tree(
        tmp_path,
        {
            "base/Dockerfile": "FROM scratch\n",
            "mid/Dockerfile": f"FROM {ref('base')}\n",
            "top/Dockerfile": f"FROM {ref('mid')}\nCOPY --from={ref('base')} /x /x\n",
        },
    )
    definitions = {name: Path(f"{name}/Dockerfile") for name in ("base", "mid", "top")}
    edges = graph(definitions, root)

    assert probe_for(edges) == ("mid", "base")
    assert generations_needed(edges) == 2


def test_a_graph_with_no_chain_needs_no_generations(tmp_path: Path) -> None:
    """A repository whose images do not build on each other floats everything."""
    root = tree(tmp_path, {"solo/Dockerfile": "FROM alpine:3.21\n"})
    edges = graph({"solo": Path("solo/Dockerfile")}, root)

    assert probe_for(edges) is None
    assert generations_needed(edges) == 0
