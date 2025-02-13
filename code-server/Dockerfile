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

# Combine RUN commands into logical groups to minimize layers and improve caching
RUN set -ex && \
    \
    # Update and upgrade package lists
    apt-get update && \
    apt-get upgrade -y && \
    \
    # Install necessary packages
    apt-get install -y --no-install-recommends \
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
        unzip \
        wget && \
    pipx ensurepath && \
    pipx install \
        black \
        isort \
        numpy \
        poetry \
        requests \
        setuptools \
        wheel && \
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

# Expose the necessary port
EXPOSE 8443
