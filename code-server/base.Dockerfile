# =============================================================================
# Base stage - Using LinuxServer's code-server as the foundation
# code-server provides VS Code functionality in the browser
# This stage sets up the core environment for our development container
# =============================================================================
FROM lscr.io/linuxserver/code-server:latest

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
        fuse3 \
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
        libfuse-dev \
        libfuse2t64 \
        libfuse3-dev \
        libgmp-dev \
        libgmp10 \
        libncurses-dev \
        libnsl-dev \
        libnsl2 \
        lsb-release \
        mininet \
        mtr \
        ninja-build \
        pkgconf \
        python-is-python3 \
        python3-full \
        python3-pip \
        python3-setuptools \
        ripgrep \
        screen \
        software-properties-common \
        strace \
        tar \
        ttyd \
        unzip \
        valgrind \
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
    find /config /tmp /var/tmp /var/lib/apt/lists -mindepth 1 -delete
