# Reference Links:
# Cloudflare WARP: https://pkg.cloudflareclient.com/
# Tailscale Installation: https://tailscale.com/kb/1031/install-linux

# Use the official Ubuntu 24.04 base image for ARM64 architecture
FROM --platform=linux/arm64 ubuntu:24.04

# Copy the entrypoint script into the container
COPY entrypoint.sh /entrypoint.sh

# Install necessary packages and set up Cloudflare and Tailscale clients
RUN apt-get update && \
    apt-get upgrade -y && \
    
    # Install required tools
    apt-get install -y curl gnupg lsb-release && \
    
    # Import the Cloudflare GPG key and add the Cloudflare APT repository
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | \
    gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | \
    tee /etc/apt/sources.list.d/cloudflare-client.list && \
    
    # Update the APT index and install the Cloudflare WARP client
    apt-get update && \
    apt-get install -y cloudflare-warp && \
    
    # Install Tailscale
    curl -fsSL https://tailscale.com/install.sh | sh && \
    
    # Clean up APT and temporary files to reduce image size
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    
    # Make the entrypoint script executable
    chmod +x /entrypoint.sh

# Set the entrypoint for the container
ENTRYPOINT ["/entrypoint.sh"]
