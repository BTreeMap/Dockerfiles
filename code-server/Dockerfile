FROM lscr.io/linuxserver/code-server:latest

# Set environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    PUID=1000 \
    PGID=1000 \
    TZ=America/Toronto \
    DEFAULT_WORKSPACE=/config/workspace \

# Combine all RUN commands into one to minimize layers
RUN set -ex && \
    \
    # Update and upgrade package lists
    apt-get update && \
    apt-get upgrade -y && \
    \
    # Install necessary packages
    apt-get install -y --no-install-recommends \
        apt-transport-https && \
        build-essential \
        curl \
        lsb-release \
        unzip \
        wget \
    \
    # Install Fast Node Manager (fnm) and Node.js version 22
    curl -fsSL https://fnm.vercel.app/install | bash && \
    . /config/.bashrc && \
    fnm install 22 && \
    \
    # Install Rust using rustup
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    \
    # Install Microsoft's OpenJDK 21
    wget https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/packages-microsoft-prod.deb -O packages-microsoft-prod.deb && \
    dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends msopenjdk-21 && \
    \
    # Clean up APT when done
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    \
    # Remove temporary and unnecessary files
    rm -rf \
        /tmp/* \
        /var/tmp/*

# Expose the necessary port
EXPOSE 8443
