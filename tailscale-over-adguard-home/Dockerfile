# Use the latest Alpine image
FROM alpine:3.20

# Install necessary packages
RUN apk update && apk upgrade && \
    apk add --no-cache \
    ca-certificates \
    libcap \
    tzdata \
    iptables \
    iproute2 \
    tini

# Set working directory
WORKDIR /opt

# Copy AdGuardHome binary from the official image
COPY --from=adguard/adguardhome:latest /opt/adguardhome/AdGuardHome /usr/local/bin/AdGuardHome

# Copy Tailscale binaries from the official image
COPY --from=tailscale/tailscale:latest /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=tailscale/tailscale:latest /usr/local/bin/tailscale /usr/local/bin/tailscale

# Set necessary capabilities for AdGuardHome to bind to privileged ports
RUN setcap 'cap_net_bind_service=+ep' /usr/local/bin/AdGuardHome

# Create necessary directories with proper permissions
RUN mkdir -p /opt/adguardhome/conf /opt/adguardhome/work /var/lib/tailscale && \
    chmod 700 /var/lib/tailscale && \
    chown -R nobody:nogroup /opt/adguardhome /var/lib/tailscale

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Environment variables for AdGuard Home
ENV ADGUARD_CONFIG_PATH="/opt/adguardhome/conf/AdGuardHome.yaml" \
    ADGUARD_WORK_DIR="/opt/adguardhome/work" \
    ADGUARD_VERBOSE="false" \
    ADGUARD_NO_CHECK_UPDATE="true" \
    ADGUARD_EXTRA_ARGS=""

# Environment variables for Tailscale
ENV TAILSCALE_AUTH_KEY="" \
    TAILSCALE_HOSTNAME="" \
    TAILSCALE_EXTRA_ARGS="" \
    TAILSCALED_EXTRA_ARGS="" \
    TAILSCALED_CUSTOM_STATE_DIR="" \
    TAILSCALE_SET_EXTRA_ARGS="--webclient"

# Environment variables for Tailscale Serve
ENV TAILSCALE_SERVE_ENABLED="false" \
    TAILSCALE_SERVE_LOCAL_PORT="80" \
    TAILSCALE_SERVE_PROTOCOL="http" \
    TAILSCALE_SERVE_EXTRA_ARGS=""

# Environment variables for Tailscale Funnel
ENV TAILSCALE_FUNNEL_ENABLED="false" \
    TAILSCALE_FUNNEL_LOCAL_PORT="53" \
    TAILSCALE_FUNNEL_PUBLIC_PORT="853" \
    TAILSCALE_FUNNEL_PROTOCOL="tls-terminated-tcp" \
    TAILSCALE_FUNNEL_EXTRA_ARGS=""

# Expose no ports

# Use Tini to handle init system and signals
ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
