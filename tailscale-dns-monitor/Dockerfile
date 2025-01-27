# Use the latest Alpine Linux image as the base
FROM alpine:latest

# Install packages, create directories, and clean up in a single layer
RUN apk update && \
    apk upgrade --no-cache && \
    apk add --no-cache \
    ca-certificates \
    iptables \
    ip6tables \
    bind-tools \
    ethtool \
    curl && \
    mkdir -p /var/run/tailscale /var/cache/tailscale /var/lib/tailscale && \
    rm -rf /var/cache/apk/*

# Set the working directory
WORKDIR /app

# Copy root filesystem and set permissions
COPY root/ /
RUN chmod +x /app/dns_query.sh /app/start.sh

# Copy Tailscale binaries in a single layer
COPY --from=tailscale/tailscale:stable \
    /usr/local/bin/tailscaled \
    /usr/local/bin/tailscale \
    /app/

# Set the entrypoint
CMD ["/app/start.sh"]
