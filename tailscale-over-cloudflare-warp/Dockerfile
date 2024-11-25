FROM --platform=linux/arm64 ubuntu:24.04

# https://pkg.cloudflareclient.com/
RUN apt-get update && apt upgrade -y
RUN apt-get install curl gnupg -y
RUN curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
RUN echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/cloudflare-client.list
RUN apt-get update && apt-get install cloudflare-warp

# https://tailscale.com/kb/1031/install-linux
RUN curl -fsSL https://tailscale.com/install.sh | sh
