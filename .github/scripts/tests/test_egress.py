"""Tests for the pure surface of the egress path.

The parts worth pinning are the ones a wrong answer makes silent: a build that
is handed no proxy looks exactly like a build that needed none, and a rules list
that routes everything looks exactly like one that routes nothing until a digest
changes. None of these tests touch the network, Docker, or Cloudflare.
"""

from __future__ import annotations

import setup_egress
from ci import egress
from ci.docker import proxy_build_args
from ci.domain import Platform, ProxyReady, ProxyUnavailable
from ci.egress import DEFAULT_PROXY_PORT, MasqueNode, bridge_address, render_config

NODE = MasqueNode(private_key="cHJpdmF0ZQ==", address_v4="172.16.0.2", address_v6="fd00::2")


class _StubProcess:
    """Stands in for a launched mihomo so start_proxy can be exercised offline."""

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None: ...


# --- build arguments -------------------------------------------------------


def test_no_proxy_url_yields_no_arguments() -> None:
    """An unproxied run must produce exactly today's command, not a longer one."""
    assert proxy_build_args(None) == ()
    assert proxy_build_args("") == ()


def test_proxy_arguments_cover_both_spellings() -> None:
    arguments = proxy_build_args("http://172.17.0.1:7890")
    assert arguments.count("--build-arg") == 6

    settings = dict(pair.split("=", 1) for pair in arguments if pair != "--build-arg")
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        assert settings[name] == "http://172.17.0.1:7890"
    assert "127.0.0.1" in settings["no_proxy"]
    assert settings["no_proxy"] == settings["NO_PROXY"]


def test_proxy_arguments_are_build_args_not_env() -> None:
    """Nothing may reach the published image; --env would be a leak."""
    assert all(
        argument.startswith("--build-arg") or "=" in argument
        for argument in proxy_build_args("http://127.0.0.1:1")
    )
    assert "--env" not in proxy_build_args("http://127.0.0.1:1")


# --- configuration ---------------------------------------------------------


def test_everything_egresses_through_warp() -> None:
    """The catch-all is the design: no upstream keeps the dirty source address.

    If this ever reverts to MATCH,DIRECT, the tunnel silently stops carrying
    anything nobody remembered to name -- which is the whack-a-mole this
    replaced, failing closed-looking rather than loudly.
    """
    rules = render_config(NODE).split("rules:", 1)[1].strip().splitlines()
    assert rules[-1].strip() == "- MATCH,warp"


def test_launchpad_needs_no_rule_of_its_own() -> None:
    """The host that started this is covered by the catch-all, not by a name."""
    config = render_config(NODE)
    assert "launchpad" not in config
    assert "- MATCH,warp" in config


def test_private_space_stays_direct() -> None:
    """Not egress. Routed into the tunnel it would fail, confusingly."""
    rules = render_config(NODE).split("rules:", 1)[1].strip().splitlines()
    direct = [rule for rule in rules if rule.strip().endswith(",DIRECT,no-resolve")]
    assert any("127.0.0.0/8" in rule for rule in direct)
    assert any("10.0.0.0/8" in rule for rule in direct)
    assert any("172.16.0.0/12" in rule for rule in direct)
    # v6 literals need the IP-CIDR6 form or mihomo rejects the config.
    assert all("IP-CIDR6," in rule for rule in direct if "::" in rule)
    assert all("IP-CIDR," in rule for rule in direct if "::" not in rule)


def test_private_ranges_are_configurable() -> None:
    config = render_config(NODE, direct_cidrs=("192.0.2.0/24",))
    assert "IP-CIDR,192.0.2.0/24,DIRECT,no-resolve" in config
    assert "10.0.0.0/8" not in config


def test_docker_hub_never_leaves_the_whitelisted_address() -> None:
    """Docker waives pull limits for GitHub runners by source IP.

    Tunnelling those pulls forfeits the waiver and drops them into the
    anonymous bucket -- 100 per six hours, shared with every other consumer on
    that WARP exit. This repository pulls ~30 base images per platform per run.
    """
    config = render_config(NODE)
    assert "  - DOMAIN-SUFFIX,docker.io,DIRECT" in config
    assert "  - DOMAIN-SUFFIX,docker.com,DIRECT" in config


def test_source_bound_entitlements_precede_the_catch_all() -> None:
    """A DIRECT rule below MATCH,warp would never be consulted."""
    rules = [r.strip() for r in render_config(NODE).split("rules:", 1)[1].strip().splitlines()]
    catch_all = rules.index("- MATCH,warp")
    for domain in ("docker.io", "ghcr.io", "github.com", "githubusercontent.com"):
        assert rules.index(f"- DOMAIN-SUFFIX,{domain},DIRECT") < catch_all


def test_direct_domains_are_configurable() -> None:
    config = render_config(NODE, direct_domains=("example.test",))
    assert "  - DOMAIN-SUFFIX,example.test,DIRECT" in config
    assert "docker.io" not in config


def test_default_port_avoids_the_conventional_ones() -> None:
    """A hosted runner is shared with whatever toolchains the matrix pulls in.

    Binding a well-known proxy port risks losing the race to something already
    holding it, and losing is silent: mihomo exits, egress degrades, and the run
    looks healthy right up until the next blackhole.
    """
    assert DEFAULT_PROXY_PORT not in {1080, 3128, 7890, 7891, 8080, 8118, 8888, 9090}
    assert DEFAULT_PROXY_PORT > 1024
    assert f"mixed-port: {DEFAULT_PROXY_PORT}" in render_config(NODE)


def test_sni_does_not_name_the_warp_service() -> None:
    """The handshake should not self-identify as WARP to anything on the path."""
    config = render_config(NODE)
    assert "sni: api.cloudflare.com" in config
    assert "masque.cloudflareclient.com" not in config
    # The destination is chosen by address, not by the name presented.
    assert "server: 162.159.198.1" in config


def test_node_addresses_reach_the_config_as_cidr() -> None:
    config = render_config(NODE, port=1234)
    assert "mixed-port: 1234" in config
    assert "ip: 172.16.0.2/32" in config
    assert "ipv6: fd00::2/128" in config
    assert 'private-key: "cHJpdmF0ZQ=="' in config


def test_listener_is_reachable_off_loopback() -> None:
    """A loopback-only bind is invisible to a RUN step in its own namespace."""
    config = render_config(NODE)
    assert "allow-lan: true" in config
    assert "bind-address: '*'" in config


# --- bridge detection ------------------------------------------------------


def test_bridge_address_falls_back_when_docker0_is_absent() -> None:
    """Never returns nothing: no bridge is a reason to guess, not to crash."""
    assert bridge_address(default="10.0.0.1") in {"10.0.0.1", bridge_address()}
    assert bridge_address().count(".") == 3


# --- host architecture -----------------------------------------------------


def test_host_platform_is_the_runner_not_the_build_target() -> None:
    """mihomo executes here, so the binary must match *this* machine.

    The two coincide in the current matrix, which is exactly what would let a
    build-target lookup pass unnoticed until something cross-builds.
    """
    assert egress.host_platform() in {Platform.AMD64, Platform.ARM64}
    assert egress.host_platform() is not None


def test_host_platform_accepts_both_spellings_of_each_arch() -> None:
    assert egress._HOST_ARCHITECTURES["x86_64"] is Platform.AMD64
    assert egress._HOST_ARCHITECTURES["amd64"] is Platform.AMD64
    assert egress._HOST_ARCHITECTURES["aarch64"] is Platform.ARM64
    assert egress._HOST_ARCHITECTURES["arm64"] is Platform.ARM64


def test_unknown_architecture_degrades_rather_than_raising(monkeypatch) -> None:
    monkeypatch.setattr(egress, "machine", lambda: "s390x")
    assert egress.host_platform() is None


# --- reachability before publication ---------------------------------------


def test_unreachable_proxy_is_never_published(monkeypatch, tmp_path) -> None:
    """Publishing an address builds cannot reach is worse than publishing none.

    apt fails hard against an unreachable proxy instead of falling back, so an
    address that is merely *listening* -- on loopback, say, invisible to a RUN
    step in its own netns -- would take every image in the repository down.
    """
    monkeypatch.setattr(egress, "_accepting", lambda port, deadline: True)
    monkeypatch.setattr(egress, "warp_egress", lambda url, timeout_seconds=15.0: False)
    monkeypatch.setattr(egress, "bridge_address", lambda default="172.17.0.1": "172.17.0.1")
    monkeypatch.setattr(egress.subprocess, "Popen", lambda *a, **k: _StubProcess())

    outcome = egress.start_proxy(tmp_path / "mihomo", NODE, tmp_path / "work")
    assert isinstance(outcome, ProxyUnavailable)
    assert "no confirmed WARP egress" in outcome.reason


def test_reachable_proxy_is_published_with_both_addresses(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(egress, "_accepting", lambda port, deadline: True)
    monkeypatch.setattr(egress, "warp_egress", lambda url, timeout_seconds=15.0: True)
    monkeypatch.setattr(egress, "bridge_address", lambda default="172.17.0.1": "172.17.0.1")
    monkeypatch.setattr(egress.subprocess, "Popen", lambda *a, **k: _StubProcess())

    outcome = egress.start_proxy(tmp_path / "mihomo", NODE, tmp_path / "work", port=29277)
    assert isinstance(outcome, ProxyReady)
    assert outcome.local_url == "http://127.0.0.1:29277"
    assert outcome.container_url == "http://172.17.0.1:29277"


def test_warp_egress_reports_false_rather_than_raising() -> None:
    """Nothing is listening on this port; the answer is False, not an exception."""
    assert egress.warp_egress("http://127.0.0.1:1", timeout_seconds=2.0) is False


def test_probe_needs_no_rule_now_that_everything_is_warped() -> None:
    """The catch-all already carries it, so no host pin is required."""
    assert egress._WARP_PROBE_HOST == "cloudflare.com"
    assert egress._WARP_PROBE_URL == "https://cloudflare.com/cdn-cgi/trace"
    assert "DOMAIN," not in render_config(NODE)


# --- the never-fails guarantee ---------------------------------------------


def test_setup_exits_zero_even_when_provisioning_explodes(monkeypatch) -> None:
    """The mitigation for a bad network day must not cost a good one.

    Cloudflare's API and MASQUE edge both have downtime; when they do, the run
    is supposed to fall back to the runner's own address, not go red.
    """

    def detonate() -> None:
        raise RuntimeError("cloudflare is having a day")

    monkeypatch.setattr(setup_egress, "provision", detonate)
    assert setup_egress.main() == 0


def test_setup_exits_zero_on_the_happy_path(monkeypatch) -> None:
    monkeypatch.setattr(setup_egress, "provision", lambda: None)
    assert setup_egress.main() == 0
