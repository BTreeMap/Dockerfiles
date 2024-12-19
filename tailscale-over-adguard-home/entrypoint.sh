#!/bin/sh

set -e

# Function to check if TUN device is available
check_tun_device() {
    if [ -c /dev/net/tun ]; then
        return 0
    else
        return 1
    fi
}

# Start tailscaled with appropriate options
TAILSCALED_ARGS=""

if [ -n "$TAILSCALED_CUSTOM_STATE_DIR" ]; then
    TAILSCALED_STATE_DIR="$TAILSCALED_CUSTOM_STATE_DIR"
else
    TAILSCALED_STATE_DIR="/var/lib/tailscale"
fi

TAILSCALED_ARGS="$TAILSCALED_ARGS --statedir=$TAILSCALED_STATE_DIR"

if check_tun_device; then
    echo "TUN device available."
else
    echo "TUN device not available, using userspace networking."
    TAILSCALED_ARGS="$TAILSCALED_ARGS --tun=userspace-networking"
fi

if [ -n "$TAILSCALED_EXTRA_ARGS" ]; then
    TAILSCALED_ARGS="$TAILSCALED_ARGS $TAILSCALED_EXTRA_ARGS"
fi

# Run tailscaled in the background
/usr/local/bin/tailscaled $TAILSCALED_ARGS &

# Wait for tailscaled to start
sleep 5

# Construct tailscale up arguments
TAILSCALE_UP_ARGS="--accept-dns=false --webclient"

if [ -n "$TAILSCALE_AUTH_KEY" ]; then
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS --authkey=${TAILSCALE_AUTH_KEY}"
fi

if [ -n "$TAILSCALE_HOSTNAME" ]; then
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS --hostname=${TAILSCALE_HOSTNAME}"
fi

if [ -n "$TAILSCALE_EXTRA_ARGS" ]; then
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS ${TAILSCALE_EXTRA_ARGS}"
fi

# Run tailscale up
/usr/local/bin/tailscale up $TAILSCALE_UP_ARGS

# Configure tailscale serve to proxy AdGuard Home web interface
if [ "$TAILSCALE_SERVE_ENABLED" = "true" ]; then
    # Ensure AdGuard Home is accessible at localhost:3000
    # Start AdGuard Home in the background temporarily to ensure the port is open
    /usr/local/bin/AdGuardHome --no-daemon $ADGUARD_ARGS &
    sleep 5
    # Setup tailscale serve
    /usr/local/bin/tailscale serve --https=443 https://localhost:3000

    # Kill the temporary AdGuardHome process
    kill %1
fi

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

# Start AdGuard Home in the foreground
exec /usr/local/bin/AdGuardHome $ADGUARD_ARGS
