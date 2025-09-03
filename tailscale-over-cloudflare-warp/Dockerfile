# Use Ubuntu 24.04 as the base image
FROM ubuntu:24.04 AS base

# Copy scripts (entrypoint, healthcheck, etc.) into the container's root directory
COPY root/ /

# Set apt frontend to non-interactive for automated installs
ARG DEBIAN_FRONTEND=noninteractive

# Install required packages, Cloudflare WARP, and Tailscale
RUN apt-get update && \
    apt-get upgrade -y && \
    # Install utilities for package management and GPG
    apt-get install -y \
      curl \
      ethtool \
      gnupg \
      iproute2 \
      iptables \
      lsb-release \
    && \
    # Import Cloudflare GPG key
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | \
    gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg && \
    # Add Cloudflare APT repository
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | \
    tee /etc/apt/sources.list.d/cloudflare-client.list && \
    # Update package index
    apt-get update && \
    # Install Cloudflare WARP client
    apt-get install -y cloudflare-warp && \
    # Install Tailscale
    curl -fsSL https://tailscale.com/install.sh | sh && \
    # Clean up apt caches and temporary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # Remove state directories for Tailscale and Cloudflare WARP
    rm -rf /var/lib/tailscale && \
    rm -rf /var/lib/cloudflare-warp && \
    # Make scripts executable
    chmod +x /entrypoint.sh && \
    chmod +x /healthcheck.sh

FROM scratch

# Copy all files from the base image
COPY --from=base / /

# Default wait durations (can be overridden at runtime)
ENV \
  TAILSCALE_INIT_SLEEP=5 \
  TAILSCALE_POST_UP_SLEEP=5 \
  WARP_INIT_SLEEP=10

# Set entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Healthcheck: run every 30s, allow 30s for startup, 10s timeout
HEALTHCHECK \
  --interval=30s \
  --start-period=30s \
  --timeout=10s \
  --retries=10 \
  CMD /healthcheck.sh
