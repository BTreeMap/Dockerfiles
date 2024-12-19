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
    ip6tables \
    tini

# Set working directory
WORKDIR /opt

# Copy AdGuardHome binary from the official image
COPY --from=adguard/adguardhome:latest /opt/adguardhome/AdGuardHome /opt/adguardhome/AdGuardHome

# Copy Tailscale binaries from the official image
COPY --from=tailscale/tailscale:latest /usr/local/bin /usr/local/bin

# Set necessary capabilities for AdGuardHome to bind to privileged ports
RUN setcap 'cap_net_bind_service=+ep' /opt/adguardhome/AdGuardHome

# Create necessary directories for AdGuardHome and Tailscale
RUN mkdir -p /opt/adguardhome/conf /opt/adguardhome/work /var/lib/tailscale && \
    chmod 700 /var/lib/tailscale

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
    TAILSCALE_EXTRA_ARGS=""

# Do not expose any ports
# EXPOSE (none)

# Use Tini to handle init system and signals
ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
