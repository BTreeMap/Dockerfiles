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
    CARGO_HOME=/opt/cargo \
    GO_HOME=/opt/go \
    EBPF_TOOLS_HOME=/opt/ebpf-tools \
    LLVM_VERSION=19 \
    LLVM_HOME=/opt/llvm

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
        bc \
        bind9-dnsutils \
        build-essential \
        ca-certificates \
        cmake \
        curl \
        git \
        gnupg \
        gzip \
        iproute2 \
        iptables \
        iputils-ping \
        jq \
        libbpf-dev \
        libbpf1 \
        libc6-dev \
        libelf-dev \
        libffi-dev \
        libffi8 \
        libgmp-dev \
        libgmp10 \
        libncurses-dev \
        libnsl-dev \
        libnsl2 \
        lsb-release \
        mininet \
        mtr \
        ninja-build \
        pipx \
        pkgconf \
        python-is-python3 \
        python3-full \
        python3-pip \
        python3-setuptools \
        python3-venv \
        screen \
        software-properties-common \
        strace \
        tar \
        unzip \
        wget \
        xvfb \
        xxd \
        xz-utils \
        zip \
        zlib1g-dev \
        zstd \
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
FROM gcc:12.4.0-bookworm AS proot_builder

# Specify the proot version to ensure reproducible builds
ARG PROOT_VERSION=v5.4.0
ARG PROOT_REPOSITORY=https://github.com/BTreeMap/proot-2025-02-26-archive.git

# Install required build dependencies:
# - git: For source code retrieval
# - clang-tools: For static analysis during build
# - libarchive-dev: For archive manipulation support
# - libtalloc-dev: For memory allocation pooling
# - Other tools for building, testing and documentation
RUN set -eux && \
    apt-get update -y && \
    apt-get upgrade -y && \
    apt-get install -y \
        clang-tools-14 \
        curl \
        docutils-common \
        gdb \
        git \
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
# Haskell is a statically typed, purely functional programming language
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
# Builder stage for Go - Used to install the Go programming language
# Go is a statically typed, compiled language designed for simplicity and efficiency.
# This stage downloads and installs the latest stable Go release for the detected architecture.
# The Go environment will be copied into the final image.
# =============================================================================
FROM base AS go_builder

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
# Builder stage for eBPF tools - Used to install the latest standalone eBPF tools.
# Currently includes bpftool, which is used for managing BPF programs and maps.
# This stage fetches the latest bpftool release for the detected architecture.
# The eBPF tools will be copied into the final image.
# =============================================================================
FROM base AS ebpf_tools_builder

RUN set -ex && \
    # Determine target architecture
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64) ARCH=amd64 ;; \
        aarch64|arm64) ARCH=arm64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;; \
    esac && \
    echo "Fetching latest bpftool version" && \
    BPFTOOL_LATEST=$(curl -sSL https://api.github.com/repos/libbpf/bpftool/releases/latest | jq -r .tag_name) && \
    echo "Latest bpftool version: $BPFTOOL_LATEST" && \
    TARBALL="bpftool-${BPFTOOL_LATEST}-${ARCH}.tar.gz" && \
    URL="https://github.com/libbpf/bpftool/releases/download/${BPFTOOL_LATEST}/${TARBALL}" && \
    echo "Downloading $URL" && \
    curl -sSL -o /tmp/${TARBALL} ${URL} && \
    curl -sSL -o /tmp/${TARBALL}.sha256sum ${URL}.sha256sum && \
    (cd /tmp && sha256sum -c ${TARBALL}.sha256sum) && \
    mkdir -p $EBPF_TOOLS_HOME && \
    tar -C $EBPF_TOOLS_HOME -xzf /tmp/${TARBALL} && \
    # Make bpftool executable
    chmod +x $EBPF_TOOLS_HOME/bpftool && \
    rm -rf /tmp/*

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
    PATH=$RACKET_HOME/bin:$CABAL_DIR/bin:$HASKELL_HOME/.ghcup/bin:$FNM_DIR:$CARGO_HOME/bin:$GO_HOME/bin:$EBPF_TOOLS_HOME:$LLVM_HOME:$PATH \
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
COPY --from=racket_builder --chown=${PUID}:${PGID} $RACKET_HOME $RACKET_HOME

# Copy the Haskell installation from the haskell_builder stage to $HASKELL_HOME in the current stage
# This allows us to use Haskell for development and execution of Haskell programs
COPY --from=haskell_builder --chown=${PUID}:${PGID} $HASKELL_HOME $HASKELL_HOME

# Copy the Go installation from the go_builder stage to $GO_HOME in the current stage
# This allows us to use Go for development and execution of Go programs
COPY --from=go_builder --chown=${PUID}:${PGID} $GO_HOME $GO_HOME

# Copy the ebpf-tools directory from the ebpf_tools_builder stage to $EBPF_TOOLS_HOME in the current stage
# This allows us to use bpftool for BPF-related operations
COPY --from=ebpf_tools_builder --chown=${PUID}:${PGID} $EBPF_TOOLS_HOME $EBPF_TOOLS_HOME

RUN set -ex && \
    \
    # Install Fast Node Manager (fnm) and Node.js
    curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell --install-dir $FNM_DIR && \
    $FNM_DIR/fnm install --lts --fnm-dir=$FNM_DIR && \
    chown -R ${PUID}:${PGID} $FNM_DIR && \
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
    chown -R ${PUID}:${PGID} $RUSTUP_HOME && \
    chown -R ${PUID}:${PGID} $CARGO_HOME && \
    \
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
    # Install the specified LLVM/Clang version
    curl -fsSL https://apt.llvm.org/llvm.sh | bash -s -- ${LLVM_VERSION} all && \
    \
    # Symlink the most popular clang/llvm commands to their -${LLVM_VERSION} variants
    mkdir -p $LLVM_HOME && \
    for tool in \
        clang clang++ clang-format clang-tidy clangd \
        lldb lld llc \
        llvm-ar llvm-as llvm-link llvm-nm llvm-objdump \
        llvm-profdata llvm-cov; \
    do \
        ln -sf /usr/bin/${tool}-${LLVM_VERSION} ${LLVM_HOME}/${tool} && command -v ${tool}; \
    done && \
    chown -R ${PUID}:${PGID} $LLVM_HOME && \
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
        orjson \
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
        reedsolo \
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
        meson \
        poetry \
        uv \
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
