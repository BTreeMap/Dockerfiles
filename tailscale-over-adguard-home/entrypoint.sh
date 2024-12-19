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

# Run tailscale set with extra arguments
if [ -n "$TAILSCALE_SET_EXTRA_ARGS" ]; then
    /usr/local/bin/tailscale set $TAILSCALE_SET_EXTRA_ARGS
fi

# Run tailscale up
/usr/local/bin/tailscale up $TAILSCALE_UP_ARGS

# Start AdGuard Home in the background
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

/usr/local/bin/AdGuardHome $ADGUARD_ARGS &

# Wait for AdGuard Home to start
sleep 5

# Configure tailscale serve to proxy AdGuard Home web interface
if [ "$TAILSCALE_SERVE_ENABLED" = "true" ]; then
    LOCAL_PORT=8443
elif echo "$TAILSCALE_SERVE_ENABLED" | grep -Eq '^[0-9]+$'; then
    # Check if it's a valid port number
    if [ "$TAILSCALE_SERVE_ENABLED" -ge 1 ] && [ "$TAILSCALE_SERVE_ENABLED" -le 65535 ]; then
        LOCAL_PORT="$TAILSCALE_SERVE_ENABLED"
    else
        echo "Invalid port number specified in TAILSCALE_SERVE_ENABLED, skipping tailscale serve configuration."
        LOCAL_PORT=""
    fi
else
    # Invalid value, do not configure tailscale serve
    echo "TAILSCALE_SERVE_ENABLED is not 'true', 'false', or a valid port number. Skipping tailscale serve configuration."
    LOCAL_PORT=""
fi

if [ -n "$LOCAL_PORT" ]; then
    /usr/local/bin/tailscale serve --bg --https=443 https+insecure://localhost:${LOCAL_PORT}
fi

# Keep the script running indefinitely to prevent container exit.
exec tail -f /dev/null
