FROM nginx:alpine-slim

# Copy Tailscale binaries from the official image
COPY --from=tailscale/tailscale:latest /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=tailscale/tailscale:latest /usr/local/bin/tailscale /usr/local/bin/tailscale

# Copy root filesystem
COPY root/ /

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

EXPOSE 80
