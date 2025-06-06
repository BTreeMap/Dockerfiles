# Reference Links:
# Cloudflare WARP: https://pkg.cloudflareclient.com/

# Use the official Ubuntu 24.04 image as the base image
FROM ubuntu:24.04 AS base

# Copy scripts (entrypoint, healthcheck, etc.) into the container's root directory
COPY root/ /

# Set the apt frontend to non-interactive for automated installs
ARG DEBIAN_FRONTEND=noninteractive

# Install dependencies, add Cloudflare repo, and install WARP client
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
    # Clean up APT caches and temporary files
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # Remove Cloudflare WARP state directory
    rm -rf /var/lib/cloudflare-warp && \
    # Make scripts executable
    chmod +x /entrypoint.sh && \
    chmod +x /healthcheck.sh

FROM scratch

# Copy all from base image to final image
COPY --from=base / /

# Set entrypoint script
ENTRYPOINT ["/entrypoint.sh"]

# Healthcheck: run every 30s, allow 30s for startup, 10s timeout
HEALTHCHECK \
  --interval=30s \
  --start-period=30s \
  --timeout=10s \
  --retries=10 \
  CMD /healthcheck.sh
