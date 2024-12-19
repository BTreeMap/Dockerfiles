#!/bin/sh

set -e

# Start Tailscaled
/usr/local/bin/tailscaled --state=/var/lib/tailscale/tailscaled.state &
sleep 5

# Construct Tailscale up arguments
TAILSCALE_UP_ARGS="--accept-dns=false"

if [ -n "$TAILSCALE_AUTH_KEY" ]; then
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS --authkey=${TAILSCALE_AUTH_KEY}"
fi

if [ -n "$TAILSCALE_HOSTNAME" ]; then
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS --hostname=${TAILSCALE_HOSTNAME}"
fi

if [ -n "$TAILSCALE_EXTRA_ARGS" ]; then
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS ${TAILSCALE_EXTRA_ARGS}"
fi

# Run Tailscale up
/usr/local/bin/tailscale up $TAILSCALE_UP_ARGS

# Construct AdGuardHome arguments
ADGUARD_ARGS=""

if [ "$ADGUARD_NO_CHECK_UPDATE" = "true" ]; then
    ADGUARD_ARGS="$ADGUARD_ARGS --no-check-update"
fi

if [ "$ADGUARD_VERBOSE" = "true" ]; then
    ADGUARD_ARGS="$ADGUARD_ARGS --verbose"
fi

ADGUARD_ARGS="$ADGUARD_ARGS -c $ADGUARD_CONFIG_PATH -w $ADGUARD_WORK_DIR"

if [ -n "$ADGUARD_EXTRA_ARGS" ]; then
    ADGUARD_ARGS="$ADGUARD_ARGS $ADGUARD_EXTRA_ARGS"
fi

# Start AdGuard Home
/opt/adguardhome/AdGuardHome $ADGUARD_ARGS
