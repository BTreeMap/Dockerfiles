"""Clean egress for a dirty runner: an ephemeral WARP tunnel behind a local proxy.

GitHub's hosted runners egress from address ranges shared by an enormous number
of tenants, and some upstreams respond to that by dropping traffic from them.
The failure this exists for was not a refusal but a silence: SYN packets to
Launchpad's `185.125.189.0/24` went unanswered, so `add-apt-repository` spent
~525s exhausting its TCP retransmit budget on four addresses before reporting,
misleadingly, that the team did not exist.

That is a property of the *machine*, not of any image, so the remedy lives here
rather than in a Dockerfile. Nothing about this reaches a published image: the
proxy is handed to builds through BuildKit's predefined proxy arguments, which
are `ARG`s rather than `ENV`s and so do not survive into the runtime
environment.

The policy is deliberately an allowlist. Everything matches `DIRECT` except the
handful of hosts known to blackhole these ranges, which keeps the bytes a build
fetches identical to what it would have fetched unproxied -- the property
`SOURCE_DATE_EPOCH` and `rewrite-timestamp` exist to protect. When some new
upstream starts dropping traffic, it is one more line in `_PROXIED_DOMAINS`.

One limitation worth stating plainly: an HTTP proxy is honoured per-program.
apt, curl, git, and Python's urllib all respect it; a tool that opens raw
sockets does not, and would need a TUN device to capture transparently. That
trade buys back the entire critical path -- registry pushes and the Actions
control plane never traverse this -- which is why it is the right one here.
"""

from __future__ import annotations

import base64
import datetime
import gzip
import hashlib
import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from platform import machine

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519

from ci.domain import EgressStatus, Platform, ProxyReady, ProxyUnavailable

# The install contract is identical to cloudflared's -- fetch a pinned asset,
# prove its digest, then make it executable -- so the outcome type is shared
# rather than duplicated under a second set of names.
from ci.tunnel import Installed, InstallFailed, InstallOutcome

logger = logging.getLogger("ci.egress")

VERSION = "v1.19.29"

# Digests are of the compressed asset, i.e. the bytes that actually arrive, so
# verification happens before anything is decompressed. Computed once from the
# pinned release and committed -- the same trust-on-first-use reasoning as
# ci/tunnel.py, and bumping VERSION means recomputing both.
_DIGESTS: dict[Platform, str] = {
    Platform.AMD64: "60de76a35a6cbf7b4fa4a20f5c257c24345d1d635ab1aa3877022a1997ef413c",
    Platform.ARM64: "9a868b5e4e0ad91d9d71e1b41b0cfce78aaba44360c30df74a723f8e3926a86c",
}

_RELEASE_URL = (
    "https://github.com/MetaCubeX/mihomo/releases/download/{version}/mihomo-linux-{arch}-{version}.gz"
)

_DOWNLOAD_CHUNK_BYTES = 1 << 20

# Consumer WARP registration. The Zero Trust variant needs an Access JWT and
# enrols against a different endpoint; this repository has no such tenant, so
# only the consumer path exists here.
_REGISTRATION_URL = "https://api.cloudflareclient.com/v0a2025/reg"
_CLIENT_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "1.1.1.1/6.32 (Android 13)",
    "CF-Client-Version": "a-6.32-3208",
}

# The registration API answers with WireGuard endpoints regardless of the key
# type enrolled. MASQUE lives on its own anycast prefix and always on UDP 443,
# so the endpoint is a constant rather than something read from the response.
_MASQUE_ENDPOINT_V4 = "162.159.198.1"
_MASQUE_PORT = 443

# Presented instead of `consumer-masque.cloudflareclient.com`, which names the
# WARP service explicitly and so makes every handshake from this runner
# trivially classifiable by anything on the path. Both names are Cloudflare's,
# and the destination is unchanged -- the anycast address above decides where
# the QUIC session actually lands, not this string.
#
# Verify this against a live tunnel before trusting it. If Cloudflare's edge
# selects the MASQUE service by SNI rather than by address, a mismatched name
# fails at handshake or certificate validation, and `handshake-timeout` above
# is then the only thing keeping that failure fast.
_MASQUE_SNI = "api.cloudflare.com"

# Deliberately obscure. A hosted runner is a shared machine running whatever
# toolchains the matrix pulls in, and the conventional proxy ports -- 1080,
# 7890, 8080, 8118 -- are exactly the ones something else will already hold. A
# collision here does not fail loudly: mihomo exits, egress silently degrades,
# and the run looks normal until an upstream blackholes again.
DEFAULT_PROXY_PORT = 29277

# Cloudflare's global static MASQUE ECDSA P-256 public key (base64 SPKI).
_CLOUDFLARE_MASQUE_PUBKEY = (
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEIaU7MToJm9NKp8YfGxR6r+/h4mcG7SxI8"
    "tsW8OR1A5tv/zCzVbCRRh2t87/kxnP6lAy0lkr7qYwu+ox+k3dr6w=="
)

_PROXY_NAME = "warp"

# The allowlist. Suffix matches, so `ppa.launchpad.net` and `api.launchpad.net`
# are both covered by the apex entry.
_PROXIED_DOMAINS: tuple[str, ...] = (
    "launchpad.net",
    "launchpadcontent.net",
)

_DEFAULT_BRIDGE_ADDRESS = "172.17.0.1"
_BRIDGE_INET = re.compile(r"\binet\s+(?P<address>\d+(?:\.\d+){3})/")


@dataclass(frozen=True, slots=True)
class MasqueNode:
    """Everything needed to write a mihomo `masque` proxy, and nothing more."""

    private_key: str
    address_v4: str
    address_v6: str


@dataclass(frozen=True, slots=True)
class RegistrationFailed:
    reason: str


RegistrationOutcome = MasqueNode | RegistrationFailed


# --- binary ----------------------------------------------------------------


# mihomo has to execute on *this machine*, which is not the same question as
# what the images are built for. The two coincide in this workflow only because
# each platform's images are built on a runner of that platform -- a coincidence
# of the matrix, not a property of the system. Reading the build target instead
# would download an unrunnable binary the moment anything cross-builds under
# QEMU, and because every failure here degrades silently, the symptom would be
# egress mysteriously never engaging rather than an error anyone could see.
_HOST_ARCHITECTURES: dict[str, Platform] = {
    "x86_64": Platform.AMD64,
    "amd64": Platform.AMD64,
    "aarch64": Platform.ARM64,
    "arm64": Platform.ARM64,
}


def host_platform() -> Platform | None:
    """The architecture of the runner itself. None if it is not one we ship for."""
    return _HOST_ARCHITECTURES.get(machine().lower())


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_DOWNLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def install(
    platform: Platform,
    destination: Path,
    version: str = VERSION,
    timeout_seconds: float = 180.0,
) -> InstallOutcome:
    """Fetches, verifies, and decompresses mihomo, reusing an existing binary.

    The digest is checked on the compressed download before a single byte is
    decompressed, and the executable bit is set only on a proven file that is
    then moved into place -- so a torn or substituted asset can never be run,
    and a failed attempt leaves nothing half-installed to be picked up next time.
    """
    expected = _DIGESTS.get(platform)
    if expected is None:
        return InstallFailed(f"no pinned digest for platform {platform}")

    if destination.exists():
        logger.info("mihomo %s already present", version)
        return Installed(path=destination, version=version)

    url = _RELEASE_URL.format(version=version, arch=platform)
    destination.parent.mkdir(parents=True, exist_ok=True)
    compressed = destination.with_suffix(".gz.partial")
    staging = destination.with_suffix(".partial")

    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout_seconds) as response:
            response.raise_for_status()
            with compressed.open("wb") as handle:
                for chunk in response.iter_bytes(_DOWNLOAD_CHUNK_BYTES):
                    handle.write(chunk)
    except (httpx.HTTPError, OSError) as error:
        compressed.unlink(missing_ok=True)
        return InstallFailed(f"download failed: {error}")

    actual = _digest_of(compressed)
    if actual != expected:
        compressed.unlink(missing_ok=True)
        return InstallFailed(f"digest mismatch: expected {expected}, got {actual}")

    try:
        with gzip.open(compressed, "rb") as source, staging.open("wb") as handle:
            shutil.copyfileobj(source, handle, _DOWNLOAD_CHUNK_BYTES)
    except (OSError, gzip.BadGzipFile) as error:
        compressed.unlink(missing_ok=True)
        staging.unlink(missing_ok=True)
        return InstallFailed(f"decompression failed: {error}")

    compressed.unlink(missing_ok=True)
    staging.chmod(staging.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    staging.replace(destination)
    logger.info("Installed mihomo %s (digest verified)", version)
    return Installed(path=destination, version=version)


def resolve_binary(platform: Platform, cache_dir: Path | None = None) -> InstallOutcome:
    """Returns a usable mihomo, preferring one already on PATH."""
    on_path = shutil.which("mihomo")
    if on_path is not None:
        return Installed(path=Path(on_path), version="preinstalled")

    root = cache_dir or Path(os.environ.get("RUNNER_TOOL_CACHE", "/tmp")) / "mihomo"
    return install(platform, root / f"mihomo-{VERSION}")


# --- registration ----------------------------------------------------------


def _ecdsa_keypair() -> str:
    """A fresh P-256 private key, DER, base64 -- the form mihomo wants."""
    key = ec.generate_private_key(ec.SECP256R1())
    der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(der).decode()


def _ecdsa_public(private_key_b64: str) -> str:
    key = serialization.load_der_private_key(base64.b64decode(private_key_b64), password=None)
    spki = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(spki).decode()


def _placeholder_wireguard_key() -> str:
    """Registration demands a WireGuard key even when enrolling for MASQUE.

    Discarded immediately: the device is upgraded to a MASQUE key type in the
    follow-up PATCH, and this value is never used to carry traffic.
    """
    private_key = x25519.X25519PrivateKey.generate()
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


def register(timeout_seconds: float = 15.0) -> RegistrationOutcome:
    """Enrols a throwaway consumer WARP device with a MASQUE key.

    Two round trips: registration mints the device identity, then a PATCH swaps
    the placeholder WireGuard key for an ECDSA one and asserts the MASQUE
    protocol. Bounded throughout -- this runs before any build, and a hung
    registration would be the same unbounded stall this module exists to remove.
    """
    private_key = _ecdsa_keypair()
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    payload = {
        "key": _placeholder_wireguard_key(),
        "install_id": "",
        "fcm_token": "",
        "tos": timestamp,
        "model": "Android",
        "serial_number": os.urandom(8).hex(),
        "os_version": "",
        "locale": "en-US",
    }

    try:
        with httpx.Client(http2=True, timeout=timeout_seconds) as client:
            response = client.post(_REGISTRATION_URL, headers=_CLIENT_HEADERS, json=payload)
            response.raise_for_status()
            registration = response.json()

            device_id = registration.get("id")
            device_token = registration.get("token")
            if not device_id or not device_token:
                return RegistrationFailed("registration returned no device id or token")

            patch_headers = {**_CLIENT_HEADERS, "Authorization": f"Bearer {device_token}"}
            patch_payload: dict[str, str] = {"key": _ecdsa_public(private_key)}

            # A device whose assigned policy is already MASQUE has its protocol
            # allocated server-side; asserting it again would fight the backend.
            if registration.get("policy", {}).get("tunnel_protocol") != "masque":
                patch_payload |= {"key_type": "masque", "tunnel_type": "masque"}

            patched = client.patch(
                f"{_REGISTRATION_URL}/{device_id}",
                headers=patch_headers,
                json=patch_payload,
            )
            patched.raise_for_status()
            config = patched.json().get("config", {})
    except (httpx.HTTPError, ValueError) as error:
        return RegistrationFailed(f"{type(error).__name__}: {error}")

    addresses = config.get("interface", {}).get("addresses", {})
    return MasqueNode(
        private_key=private_key,
        address_v4=addresses.get("v4") or "172.16.0.2",
        address_v6=addresses.get("v6") or "fd00::2",
    )


# --- configuration ---------------------------------------------------------


def render_config(
    node: MasqueNode,
    port: int = DEFAULT_PROXY_PORT,
    proxied_domains: tuple[str, ...] = _PROXIED_DOMAINS,
) -> str:
    """Renders the mihomo configuration. Pure, so the policy is testable.

    `MATCH,DIRECT` last is the whole design: only the named domains traverse
    WARP, and every other fetch takes the path it would have taken with this
    module absent.
    """
    rules = "\n".join(
        f"  - DOMAIN-SUFFIX,{domain},{_PROXY_NAME}" for domain in proxied_domains
    )
    return f"""mixed-port: {port}
allow-lan: true
bind-address: '*'
mode: rule
log-level: warning
external-controller: ''
geo-auto-update: false
proxies:
  - name: "{_PROXY_NAME}"
    type: masque
    server: {_MASQUE_ENDPOINT_V4}
    port: {_MASQUE_PORT}
    sni: {_MASQUE_SNI}
    private-key: "{node.private_key}"
    public-key: "{_CLOUDFLARE_MASQUE_PUBKEY}"
    ip: {node.address_v4}/32
    ipv6: {node.address_v6}/128
    mtu: 1280
    udp: true
    network: quic
    remote-dns-resolve: true
    dns: [ 1.1.1.1, 1.0.0.1 ]
    handshake-timeout: 30
    congestion-controller: bbr
rules:
{rules}
  - MATCH,DIRECT
"""


def bridge_address(default: str = _DEFAULT_BRIDGE_ADDRESS) -> str:
    """The runner's address on the docker bridge, as seen from inside a build.

    A RUN step executes in its own network namespace behind buildkitd, so the
    runner's loopback is not the runner from in there. The bridge gateway is,
    and it is the only address that reaches a host listener from both sides.
    """
    try:
        result = subprocess.run(
            ("ip", "-4", "addr", "show", "docker0"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return default

    match = _BRIDGE_INET.search(result.stdout)
    return match.group("address") if match else default


# --- lifetime --------------------------------------------------------------


def _accepting(port: int, deadline: float) -> bool:
    """Waits for the listener, which is the only real proof mihomo came up."""
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# Answers 204 with an empty body, so this costs one round trip and no payload.
_REACHABILITY_URL = "http://cp.cloudflare.com/generate_204"


def relays(proxy_url: str, timeout_seconds: float = 10.0) -> bool:
    """Proves the proxy actually relays *at the address its consumer will use*.

    This is the check that makes the whole thing safe to publish. A listener on
    loopback satisfies `_accepting` while being invisible to a RUN step in its
    own network namespace -- and exporting an address the builds cannot reach
    would turn a best-effort improvement into a repository-wide outage, because
    apt fails hard against an unreachable proxy rather than falling back to a
    direct connection.

    So the container-facing URL is exercised end to end before anyone is told
    about it. The target resolves through `MATCH,DIRECT`, which keeps this a
    test of the local relay rather than of WARP: a healthy proxy whose tunnel is
    down is still strictly better than no proxy at all.
    """
    try:
        with httpx.Client(proxy=proxy_url, timeout=timeout_seconds) as client:
            return client.get(_REACHABILITY_URL).status_code in {200, 204}
    except (httpx.HTTPError, OSError):
        return False


def start_proxy(
    binary: Path,
    node: MasqueNode,
    working_dir: Path,
    port: int = DEFAULT_PROXY_PORT,
    startup_timeout_seconds: float = 30.0,
) -> EgressStatus:
    """Starts mihomo detached, to live as long as the job does.

    Deliberately not a context manager, unlike `ci.tunnel.quick_tunnel`. That
    one belongs to a single process and must not outlive it; this one is
    provisioned by one step and consumed by later ones, so a scope that ended
    with the provisioning process would tear the proxy down before its first
    user ran. Its owner is the job, and the runner reaps the session on
    teardown -- which is sound precisely because these runners are ephemeral
    and single-tenant.

    Output goes to the void rather than the log. The configuration holds a
    private key, and on a public repository the job log is world-readable as it
    is being written.
    """
    working_dir.mkdir(parents=True, exist_ok=True)
    config = working_dir / "config.yaml"

    # Written 0600 and left in place: mihomo re-reads it, and the file lives on
    # a single-tenant VM that is destroyed with the job. Deleting it post-start
    # would buy nothing and break a reload.
    config.touch(mode=0o600, exist_ok=True)
    config.write_text(render_config(node, port), encoding="utf-8")

    try:
        subprocess.Popen(
            [str(binary), "-f", str(config), "-d", str(working_dir)],
            # All three streams are detached, and not only to keep the private
            # key out of a world-readable log. The Actions runner collects a
            # step's output through pipes and waits for EOF on them; a
            # background child that inherits those descriptors holds them open
            # after the step's own command returns, and the step then hangs
            # until it is killed. Inheriting stdin risks the same stall against
            # a prompt nobody can answer.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Its own session, so the runner tearing down this step's process
            # group does not take the proxy with it -- the whole point is to
            # outlive the step that starts it. The runner still reaps orphans
            # during "Complete job", which is exactly the lifetime wanted: one
            # job, then gone.
            start_new_session=True,
        )
    except OSError as error:
        return ProxyUnavailable(f"could not start mihomo: {error}")

    if not _accepting(port, deadline=time.monotonic() + startup_timeout_seconds):
        return ProxyUnavailable("mihomo did not accept connections before the deadline")

    container_url = f"http://{bridge_address()}:{port}"
    if not relays(container_url):
        # Listening but not usable from where it matters. Refusing to publish it
        # is the whole point: a build that cannot reach its configured proxy
        # fails, where a build with no proxy configured simply carries on.
        return ProxyUnavailable(f"proxy does not relay at {container_url}")

    logger.info("Local proxy listening on port %d and relaying", port)
    return ProxyReady(local_url=f"http://127.0.0.1:{port}", container_url=container_url)
