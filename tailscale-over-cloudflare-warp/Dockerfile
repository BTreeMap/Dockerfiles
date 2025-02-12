# Reference Links:
# Cloudflare WARP: https://pkg.cloudflareclient.com/
# Tailscale Installation: https://tailscale.com/kb/1031/install-linux

# Use the official Ubuntu 24.04 base image for ARM64 architecture
FROM ubuntu:24.04

# Copy the entrypoint script into the container's root directory
COPY entrypoint.sh /entrypoint.sh

# Set the apt frontend to non-interactive mode for automated installs
ARG DEBIAN_FRONTEND=noninteractive

# Install necessary packages and configure the environment for Cloudflare and Tailscale clients
RUN apt-get update && \
    apt-get upgrade -y && \
    # Install required utilities for package management and GPG handling
    apt-get install -y curl gnupg lsb-release ethtool && \
    # Import the Cloudflare GPG key for package verification
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | \
    gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg && \
    # Add the Cloudflare APT repository to the sources list
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | \
    tee /etc/apt/sources.list.d/cloudflare-client.list && \
    # Update the package index after adding the new repository
    apt-get update && \
    # Install the Cloudflare WARP client
    apt-get install -y cloudflare-warp && \
    # Download and install Tailscale using its installation script
    curl -fsSL https://tailscale.com/install.sh | sh && \
    # Clean up APT caches and temporary files to minimize the final image size
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    # Remove the state directories for Tailscale and Cloudflare WARP to ensure they are empty in the image
    rm -rf /var/lib/tailscale && \
    rm -rf /var/lib/cloudflare-warp && \
    # Make the entrypoint script executable to ensure it can be run at container start
    chmod +x /entrypoint.sh && \
    # Create necessary directories for WARP and pre-accept TOS (terms of service)
    mkdir -p /home/ubuntu/.local/share/warp && \
    echo -n 'yes' > /home/ubuntu/.local/share/warp/accepted-tos.txt && \
    echo -n 'yes' > /home/ubuntu/.local/share/warp/accepted-teams-tos.txt

# Set the entrypoint for the container to execute the entrypoint script when the container starts
ENTRYPOINT ["/entrypoint.sh"]
