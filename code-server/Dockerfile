# =============================================================================
# Builder stage for proot - Used to compile the proot binary from source
# proot allows for containerized chroot-like functionality without root privileges
# =============================================================================
FROM gcc:7.4.0 as proot_builder

# Specify the proot version to ensure reproducible builds
ARG PROOT_VERSION=v5.4.0

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
RUN git clone --depth 1 --branch $PROOT_VERSION https://github.com/proot-me/proot /proot

# Set the working directory for build operations
WORKDIR /proot

# Compile proot with the 'care' utility (creates portable archives of programs)
RUN make -C src proot care

# Compile a static version of proot for maximum portability
# Static linking ensures the binary can run without external dependencies
# The resulting binary will be copied to the final image
RUN LDFLAGS="${LDFLAGS} -static" make -C src proot GIT=false && \
    mkdir -p dist && \
    cp src/proot dist/

# =============================================================================
# Main image - Using LinuxServer's code-server as the base image
# code-server provides VS Code in the browser with a full development environment
# =============================================================================
FROM lscr.io/linuxserver/code-server:latest

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    PUID=1000 \
    PGID=1000 \
    TZ=America/Toronto \
    DEFAULT_WORKSPACE=/config/workspace \
    FNM_DIR=/opt/fnm \
    FNM_MULTISHELL_PATH=/opt/fnm/multishells \
    RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo

# Environment variables for Tailscale
ENV TAILSCALE_ENABLED=true \
    TAILSCALE_AUTHKEY="" \
    TAILSCALE_HOSTNAME=code-server \
    TAILSCALE_USE_SSH=false \
    TAILSCALE_ACCEPT_DNS=true \
    TAILSCALE_AUTO_UPDATE=false \
    TS_BE_EXIT_NODE=false \
    TAILSCALE_USE_WEBCLIENT=true \
    TAILSCALE_STATE_DIR=/config/tailscale \
    TAILSCALE_SERVE_MODE=https \
    TAILSCALE_SERVE_PORT=8443

# Environment variables for pipx
ARG PIPX_HOME=/opt/pipx \
    PIPX_BIN_DIR=/usr/local/bin \
    PIPX_MAN_DIR=/usr/local/share/man

# Copy application files and s6-overlay config to container root
COPY root/ /

# Copy the proot binary from the proot_builder stage to /usr/local/bin/proot in the current stage
# This allows us to use proot for creating a virtualized filesystem environment without full root privileges
COPY --from=proot_builder /proot/dist/proot /usr/local/bin/proot

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
        lsb-release \
        mtr \
        pipx \
        python-is-python3 \
        python3-full \
        python3-pip \
        python3-setuptools \
        python3-venv \
        unzip \
        wget && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /config/* \
        /tmp/* \
        /var/tmp/*

RUN set -ex && \
    \
    # Install Fast Node Manager (fnm) and Node.js
    curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell --install-dir $FNM_DIR && \
    echo '#!/bin/sh' > /etc/profile.d/fnm.sh && \
    chmod +x /etc/profile.d/fnm.sh && \
    echo "export PATH=\"$FNM_DIR:\$PATH\"" >> /etc/profile.d/fnm.sh && \
    $FNM_DIR/fnm install --lts --fnm-dir=$FNM_DIR && \
    echo 'eval "$($FNM_DIR/fnm env)"' >> /etc/profile.d/fnm.sh && \
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
    echo '#!/bin/sh' > /etc/profile.d/rust.sh && \
    chmod +x /etc/profile.d/rust.sh && \
    echo "export PATH=\"$CARGO_HOME/bin:\$PATH\"" >> /etc/profile.d/rust.sh && \
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
    # Install some Python packages using pip
    python3 -m venv /opt/shared-venv --upgrade-deps && \
    /opt/shared-venv/bin/pip install --no-cache-dir \
        aiodns \
        aiohttp \
        beautifulsoup4 \
        black \
        click \
        cryptography \
        Django \
        Flask \
        httpx[brotli,http2,socks,zstd] \
        isort \
        jupyter \
        matplotlib \
        numpy \
        openai \
        pandas \
        pillow \
        psycopg[binary] \
        pydantic \
        pymongo \
        pytest \
        python-dateutil \
        python-dotenv \
        python-telegram-bot \
        PyYAML \
        requests[socks] \
        scapy \
        scikit-learn \
        scipy \
        sympy \
        ujson \
        websockets && \
    \
    # Install some Python applications using pipx
    pipx install --pip-args='--no-cache-dir' \
        black \
        httpie \
        isort \
        poetry \
        wheel && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* \
        /config/* \
        /tmp/* \
        /var/tmp/*

# Expose the necessary port
EXPOSE 8443
