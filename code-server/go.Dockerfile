# =============================================================================
# Standalone build of the Go toolchain, published as :code-server-go
#
# Extracted verbatim from the go_builder stage of ./Dockerfile so it can be
# built as its own task, in parallel with the other toolchains, instead of
# serially inside the main image. The stage remains in ./Dockerfile for now;
# that copy is retired once this tag exists in the registry.
#
# Downloads and installs the latest stable Go release for the detected
# architecture.
# =============================================================================
# The batch this build is pinned to, empty by default so this file still builds
# standalone against the floating tag. Only the batch is negotiable -- the image
# is literal above, so a build argument can sharpen this reference but never
# redirect it. See ci/domain.selector.
# One argument per reference from this repository, each defaulting to the
# reference itself. Without the build system this file resolves exactly these
# images; with it, every argument is replaced by the same image in the registry
# being published to, pinned to a batch. The argument names are free: the build
# system reads each one off the declaration it appears in.
ARG CODE_SERVER_BASE=ghcr.io/btreemap/dockerfiles:code-server-base
FROM ${CODE_SERVER_BASE} AS go_builder

RUN set -ex && \
    # Determine target architecture
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64) ARCH=amd64 ;; \
        aarch64|arm64) ARCH=arm64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;; \
    esac && \
    # Fetch Go releases JSON and extract latest stable version and checksum for our arch
    GO_JSON="$(curl -sSfL 'https://go.dev/dl/?mode=json')" && \
    GO_VERSION="$(echo "$GO_JSON" | jq -r '[.[] | select(.stable)][0].version')" && \
    TARBALL="${GO_VERSION}.linux-${ARCH}.tar.gz" && \
    GO_SHA256="$(echo "$GO_JSON" | jq -r --arg GO_VERSION "$GO_VERSION" --arg TARBALL "$TARBALL" '.[] | select(.version == $GO_VERSION) | .files[] | select(.filename == $TARBALL) | .sha256')" && \
    echo "Installing ${GO_VERSION} for ${ARCH}" && \
    curl -sSfL -o /tmp/go.tgz "https://go.dev/dl/${TARBALL}" && \
    echo "${GO_SHA256}  /tmp/go.tgz" | sha256sum -c - && \
    tar -C /opt -xzf /tmp/go.tgz && \
    rm -rf /tmp/go.tgz && \
    $GO_HOME/bin/go version

# =============================================================================
# Artifact image - carries the installation and nothing else
#
# Scratch rather than the builder: this tag exists only to be read by a
# COPY --from, so republishing all of code-server-base underneath it twice a
# day would buy nothing.
#
# /opt/go is code-server-base's $GO_HOME, repeated literally because a scratch
# stage inherits no ENV. If the two ever diverge this COPY fails the build
# outright rather than publishing an empty image.
# =============================================================================
FROM scratch

COPY --from=go_builder /opt/go /opt/go
