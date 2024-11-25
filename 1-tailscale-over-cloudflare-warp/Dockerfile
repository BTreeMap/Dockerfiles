# Reference:
# https://pkg.cloudflareclient.com/
# https://tailscale.com/kb/1031/install-linux

FROM --platform=linux/arm64 ubuntu:24.04

# Install necessary packages and add repositories
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y curl gnupg && \
    curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list && \
    apt-get update && apt-get install -y cloudflare-warp && \
    curl -fsSL https://tailscale.com/install.sh | sh
