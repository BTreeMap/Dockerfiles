# Use the latest Alpine Linux image as the base
FROM alpine:latest

# Install packages and create directories in a single layer
RUN apk update && \
    apk add --no-cache \
    ca-certificates \
    iptables \
    ip6tables \
    bind-tools \
    ethtool \
    curl && \
    mkdir -p /var/run/tailscale /var/cache/tailscale /var/lib/tailscale

# Set the working directory
WORKDIR /app

# Copy root filesystem
COPY root/ /

# Make scripts executable
RUN chmod +x /app/dns_query.sh /app/start.sh

# Copy both Tailscale binaries in a single layer
COPY --from=tailscale/tailscale:latest /usr/local/bin/tailscaled /usr/local/bin/tailscale /app/

# Set the entrypoint to the start.sh script
CMD ["/app/start.sh"]
