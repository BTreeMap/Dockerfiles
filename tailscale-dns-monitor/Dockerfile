# Use the latest Alpine Linux image as the base
FROM alpine:latest

# Install required packages
RUN apk update && \
    apk add --no-cache \
    ca-certificates \
    iptables \
    ip6tables \
    bind-tools \
    ethtool \
    curl

# Create application directory
WORKDIR /app

# Copy application scripts and configuration files
COPY dns_query.sh /app/dns_query.sh
COPY domains.txt /app/domains.txt
COPY dns_servers.txt /app/dns_servers.txt
COPY start.sh /app/start.sh

# Make scripts executable
RUN chmod +x /app/dns_query.sh /app/start.sh

# Copy Tailscale binaries from the official Tailscale image
COPY --from=tailscale/tailscale:latest /usr/local/bin/tailscaled /app/tailscaled
COPY --from=tailscale/tailscale:latest /usr/local/bin/tailscale /app/tailscale

# Create necessary directories for Tailscale
RUN mkdir -p /var/run/tailscale /var/cache/tailscale /var/lib/tailscale

# Expose any necessary ports (if needed)
# EXPOSE 53

# Set the entrypoint to the start.sh script
CMD ["/app/start.sh"]
