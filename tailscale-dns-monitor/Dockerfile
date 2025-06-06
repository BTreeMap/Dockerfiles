# Base image - lightweight Alpine Linux for minimal container size
FROM alpine:3.21 AS base

# System setup: Update, install dependencies, create directories, and clean cache
RUN apk update && \
    apk upgrade && \
    apk add \
        bind-tools \
        ca-certificates \
        curl \
        ethtool \
        iproute2 \
        iptables \
    && \
    mkdir -p /var/run/tailscale /var/cache/tailscale /var/lib/tailscale && \
    rm -rf /var/cache/apk/*

# Copy application files and make scripts executable
COPY root/ /
RUN chmod +x /app/dns_query.sh /app/start.sh

FROM scratch

# Copy the base image contents to the final image
COPY --from=base / /

# Import Tailscale binaries directly from official image for reliability
COPY --from=tailscale/tailscale:stable \
    /usr/local/bin/tailscaled \
    /usr/local/bin/tailscale \
    /app/

# Set default container working directory for script execution
WORKDIR /app

# Tailscale configuration environment variables
ENV TAILSCALE_AUTH_KEY="" \
    TAILSCALE_HOSTNAME="" \
    TAILSCALE_EXTRA_ARGS="" \
    TAILSCALED_EXTRA_ARGS="" \
    TAILSCALED_CUSTOM_STATE_DIR="" \
    TAILSCALE_SET_EXTRA_ARGS="--webclient"

# DNS monitoring configuration variables
ENV QUERY_DURATION=1 \
    REFRESH_DURATION=300 \
    DNS_SERVERS_FILE=/data/dns_servers.txt \
    DOMAINS_FILE=/data/domains.txt

# Container entrypoint - starts both Tailscale and monitoring services
CMD ["/app/start.sh"]
