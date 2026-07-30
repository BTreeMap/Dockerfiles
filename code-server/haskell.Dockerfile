# =============================================================================
# Standalone build of the Haskell toolchain, published as :code-server-haskell
#
# Extracted verbatim from the haskell_builder stage of ./Dockerfile so it can be
# built as its own task, in parallel with the other toolchains, instead of
# serially inside the main image. The stage remains in ./Dockerfile for now;
# that copy is retired once this tag exists in the registry.
#
# Sets up GHC (compiler), Cabal (package manager), and HLS (language server).
# =============================================================================
FROM ghcr.io/btreemap/dockerfiles:code-server-base AS haskell_builder

# Set environment variables for Haskell installation
ENV GHCUP_HOME=/opt/ghcup \
    GHCUP_INSTALL_BASE_PREFIX=$HASKELL_HOME

# Set the working directory for Haskell installation
WORKDIR $GHCUP_HOME

# Download the appropriate GHCup binary based on architecture
RUN set -ex && \
    # Determine target architecture
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64) ARCH=x86_64 ;; \
        aarch64|arm64) ARCH=aarch64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;; \
    esac && \
    # Download GHCup installer
    GHCUP_URL="https://downloads.haskell.org/~ghcup/${ARCH}-linux-ghcup" && \
    curl -sSL "$GHCUP_URL" -o ghcup && \
    chmod +x ghcup

# Install GHC, Cabal, and HLS using GHCup
RUN ./ghcup install ghc --set recommended && \
    ./ghcup install cabal latest && \
    ./ghcup install hls latest && \
    ./ghcup gc --cache --hls-no-ghc --profiling-libs --tmpdirs && \
    rm -rf \
        $HASKELL_HOME/.ghcup/cache \
        $HASKELL_HOME/.ghcup/logs \
        $HASKELL_HOME/.ghcup/tmp \
        $HASKELL_HOME/.ghcup/trash

# =============================================================================
# Artifact image - carries the installation and nothing else
#
# Scratch rather than the builder: this tag exists only to be read by a
# COPY --from, so republishing all of code-server-base underneath it twice a
# day would buy nothing.
#
# /opt/haskell is code-server-base's $HASKELL_HOME, repeated literally because a
# scratch stage inherits no ENV. If the two ever diverge this COPY fails the
# build outright rather than publishing an empty image. $GHCUP_HOME is
# deliberately not carried: ./Dockerfile copies only $HASKELL_HOME.
# =============================================================================
FROM scratch

COPY --from=haskell_builder /opt/haskell /opt/haskell
