# =============================================================================
# Base stage - Using LinuxServer's code-server as the foundation
# code-server provides VS Code functionality in the browser
# This stage sets up the core environment for our development container
# =============================================================================
FROM lscr.io/linuxserver/code-server:latest AS base

# Set environment variables for the base image
ENV DEBIAN_FRONTEND=noninteractive \
    PUID=1000 \
    PGID=1000 \
    TZ=America/Toronto \
    DEFAULT_WORKSPACE=/config/workspace

# Set environment variables for Racket, FNM, Rust, and Cargo
ENV RACKET_HOME=/opt/racket \
    CABAL_DIR=/config/.haskell \
    HASKELL_HOME=/opt/haskell \
    FNM_DIR=/opt/fnm \
    RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo

# Combine RUN commands into logical groups to minimize layers and improve caching
RUN set -ex && \
    \
    # Update and upgrade package lists
    apt-get update && \
    apt-get upgrade -y && \
    \
    # Install necessary packages
    apt-get install -y --no-install-recommends \
        7zip \
        apt-transport-https \
        bind9-dnsutils \
        build-essential \
        curl \
        git \
        iputils-ping \
        libgmp-dev \
        lsb-release \
        mtr \
        pipx \
        python-is-python3 \
        python3-full \
        python3-pip \
        python3-setuptools \
        python3-venv \
        screen \
        unzip \
        wget \
        xvfb \
        xz-utils \
    && \
    # Remove temporary and unnecessary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /config/* \
        /tmp/* \
        /var/tmp/*

# =============================================================================
# Builder stage for proot - Used to compile the proot binary from source
# proot allows for containerized chroot-like functionality without root privileges
# =============================================================================
FROM gcc:7.4.0 AS proot_builder

# Specify the proot version to ensure reproducible builds
ARG PROOT_VERSION=v5.4.0
ARG PROOT_REPOSITORY=https://github.com/BTreeMap/proot-2025-02-26-archive.git

# Install required build dependencies:
# - git: For source code retrieval
# - clang-tools: For static analysis during build
# - libarchive-dev: For archive manipulation support
# - libtalloc-dev: For memory allocation pooling
# - Other tools for building, testing and documentation
RUN apt-get update -qq && \
    apt-get upgrade -qq && \
    apt-get install -qq \
    git \
    clang-tools-6.0 \
    curl \
    docutils-common \
    gdb \
    lcov \
    libarchive-dev \
    libtalloc-dev \
    strace \
    swig \
    uthash-dev \
    xsltproc

# Clone the specific version of proot from GitHub
# Using --depth 1 to minimize download size (shallow clone)
RUN git clone --depth 1 --branch $PROOT_VERSION $PROOT_REPOSITORY /proot

# Set the working directory for build operations
WORKDIR /proot

# Compile a static version of proot for maximum portability
# Static linking ensures the binary can run without external dependencies
# The resulting binary will be copied to the final image
RUN LDFLAGS="${LDFLAGS} -static" make -C src proot GIT=false && \
    mkdir -p dist && \
    cp src/proot dist/

# =============================================================================
# Builder stage for Racket - Used to compile Racket programming language from source
# Racket is a general-purpose, multi-paradigm programming language in the Lisp/Scheme family
# This stage builds Racket which will be copied into the final image
# =============================================================================
FROM base AS racket_builder

# Set environment variables for Racket installation
ENV BUILD_DIR=/build

# Specify the Racket version and download URL
ARG RACKET_VERSION=8.16
ARG RACKET_PACKAGE=racket-8.16-src-builtpkgs.tgz
ARG RACKET_URL=https://download.racket-lang.org/releases/$RACKET_VERSION/installers/$RACKET_PACKAGE
ARG RACKET_CHECKSUM=sha256:44d7c1ab34b52588f90dc22b15d96110e104d0c88ed1869f85b6f03c99843078

# Download the Racket source package
ADD --checksum=$RACKET_CHECKSUM $RACKET_URL $BUILD_DIR/$RACKET_PACKAGE

# Set the working directory for build operations
WORKDIR $BUILD_DIR

# Extract, configure, build, and install Racket
RUN tar xfz $RACKET_PACKAGE && \
    cd racket-$RACKET_VERSION/src && \
    ./configure --prefix=$RACKET_HOME && \
    make -j$(nproc) && \
    make install

# =============================================================================
# Builder stage for Haskell - Used to install the Haskell toolchain
# Haskell is a statically-typed, purely functional programming language
# This stage sets up GHC (compiler), Cabal (package manager), and HLS (language server)
# The Haskell environment will be copied into the final image
# =============================================================================
FROM base AS haskell_builder

# Set environment variables for Haskell installation
ENV GHCUP_HOME=/opt/ghcup \
    GHCUP_INSTALL_BASE_PREFIX=$HASKELL_HOME

# Set the working directory for Haskell installation
WORKDIR $GHCUP_HOME

# Download the appropriate GHCup binary based on architecture
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then \
        GHCUP_URL="https://downloads.haskell.org/~ghcup/aarch64-linux-ghcup"; \
    else \
        GHCUP_URL="https://downloads.haskell.org/~ghcup/x86_64-linux-ghcup"; \
    fi && \
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
# Main image - Using LinuxServer's code-server as the base image
# code-server provides VS Code in the browser with a full development environment
# =============================================================================
FROM base

# Set environment variables for the final image
ENV CABAL_UPDATE_PACKAGES=true \
    PATCH_JSON=true \
    PATCH_JSON_ENABLE_SORT_KEYS=true \
    PATCH_JSON_USE_FORCE=true \
    PATH=$RACKET_HOME/bin:$CABAL_DIR/bin:$HASKELL_HOME/.ghcup/bin:$FNM_DIR:$CARGO_HOME/bin:$PATH \
    USER_INIT_DIR=/config/.entrypoint.d \
    USER_INIT_FILE=/config/.entrypoint \
    VENV_DIR=/opt/shared-venv

# Environment variables for Tailscale
ENV \
    TAILSCALE_ENABLED=false \
    TAILSCALE_STATE_DIR=/config/tailscale \
    TAILSCALE_DISABLE_LOGGING=false \
    TAILSCALE_AUTHKEY="" \
    TAILSCALE_HOSTNAME=code-server \
    TAILSCALE_USE_SSH=false \
    TAILSCALE_ACCEPT_DNS=true \
    TAILSCALE_REPLACE_DNS=false \
    TAILSCALE_AUTO_UPDATE=false \
    TAILSCALE_BE_EXIT_NODE=false \
    TAILSCALE_USE_WEBCLIENT=true \
    TAILSCALE_SERVE_MODE=https \
    TAILSCALE_SERVE_PORT=8443

# Environment variables for pipx
ARG PIPX_HOME=/opt/pipx \
    PIPX_BIN_DIR=/usr/local/bin \
    PIPX_MAN_DIR=/usr/local/share/man

## Copy application files and s6-overlay config to container root
COPY root/ /

# Copy the proot binary from the proot_builder stage to /usr/local/bin/proot in the current stage
# This allows us to use proot for creating a virtualized filesystem environment without full root privileges
COPY --from=proot_builder /proot/dist/proot /usr/local/bin/proot

# Copy the Racket installation from the racket_builder stage to $RACKET_HOME in the current stage
# This allows us to use Racket for development and execution of Racket programs
COPY --from=racket_builder $RACKET_HOME $RACKET_HOME

# Copy the Haskell installation from the haskell_builder stage to $HASKELL_HOME in the current stage
# This allows us to use Haskell for development and execution of Haskell programs
COPY --from=haskell_builder $HASKELL_HOME $HASKELL_HOME

RUN set -ex && \
    \
    # Install Fast Node Manager (fnm) and Node.js
    curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell --install-dir $FNM_DIR && \
    $FNM_DIR/fnm install --lts --fnm-dir=$FNM_DIR && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /config/* \
        /tmp/* \
        /var/tmp/*

RUN set -ex && \
    \
    # Install Rust using rustup
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    # Remove temporary and unnecessary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /config/* \
        /tmp/* \
        /var/tmp/*

RUN set -ex && \
    \
    # Install Microsoft's OpenJDK 21
    wget https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/packages-microsoft-prod.deb -O packages-microsoft-prod.deb && \
    dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends msopenjdk-21 && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /config/* \
        /tmp/* \
        /var/tmp/*

RUN set -ex && \
    \
    # Install Tailscale
    curl -fsSL https://tailscale.com/install.sh | sh && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /config/* \
        /tmp/* \
        /var/tmp/*

RUN set -ex && \
    \
    # Install some Python packages using pip
    # Create Python virtual environment as root then change ownership
    python3 -m venv ${VENV_DIR} --upgrade-deps && \
    chown -R ${PUID}:${PGID} ${VENV_DIR} && \
    # Install packages as the specified user
    s6-setuidgid ${PUID}:${PGID} \
        ${VENV_DIR}/bin/pip install --no-cache-dir \
        aiodns \
        aiohttp \
        aioitertools \
        aiomultiprocess \
        aiosqlite \
        anthropic \
        beautifulsoup4 \
        black \
        click \
        cohere \
        cryptography \
        discord.py \
        Django \
        Flask \
        google-cloud-firestore \
        google-cloud-storage \
        google-genai \
        httpx[brotli,http2,socks,zstd] \
        isort \
        json5 \
        jupyter \
        langchain \
        langchain-community \
        langgraph \
        loguru \
        matplotlib \
        numpy \
        openai \
        pandas \
        pillow \
        psycopg[binary,pool] \
        pydantic \
        pymongo \
        pypdf \
        pytest \
        python-dateutil \
        python-dotenv \
        python-telegram-bot \
        PyYAML \
        requests[socks] \
        scapy \
        scikit-learn \
        scipy \
        seaborn \
        skl2onnx \
        sympy \
        tqdm \
        twilio \
        ujson \
        websockets \
    && \
    # Install some Python applications using pipx
    pipx install --pip-args='--no-cache-dir' \
        black \
        httpie \
        isort \
        poetry \
        wheel \
    && \
    # Remove temporary and unnecessary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /config/* \
        /tmp/* \
        /var/tmp/*

# Expose the necessary port
EXPOSE 8443
