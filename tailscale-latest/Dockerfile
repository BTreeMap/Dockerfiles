FROM tailscale/tailscale:stable

# Upgrade apk packages
RUN apk upgrade --no-cache

# Set the required environment variables
ENV TS_AUTH_ONCE=true \
    TS_ACCEPT_DNS=false \
    TS_STATE_DIR=/var/lib/tailscale \
    TS_USERSPACE=false
