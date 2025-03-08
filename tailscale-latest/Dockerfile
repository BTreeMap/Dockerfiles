FROM tailscale/tailscale:stable

# Update and upgrade apk packages, then remove cache to reduce image size
RUN apk update && apk upgrade && rm -rf /var/cache/apk/*

# Set the required environment variables
ENV TS_AUTH_ONCE=true \
    TS_ACCEPT_DNS=false \
    TS_STATE_DIR=/var/lib/tailscale \
    TS_USERSPACE=false
