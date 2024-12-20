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

# Configure tailscale serve to proxy services
if [ "$TAILSCALE_SERVE_ENABLED" = "true" ]; then
    # Default values if not set
    : "${TAILSCALE_SERVE_LOCAL_PORT:=8443}"
    : "${TAILSCALE_SERVE_PROTOCOL:=https+insecure}"
    
    # Start AdGuard Home or ensure the service to be proxied is running
    # (Assuming AdGuard Home is already started before this point)

    # Use TAILSCALE_SERVE_EXTRA_ARGS if provided
    if [ -n "$TAILSCALE_SERVE_EXTRA_ARGS" ]; then
        echo "Using custom tailscale serve arguments: $TAILSCALE_SERVE_EXTRA_ARGS"
        /usr/local/bin/tailscale serve $TAILSCALE_SERVE_EXTRA_ARGS
    else
        echo "Configuring tailscale serve with protocol ${TAILSCALE_SERVE_PROTOCOL} and local port ${TAILSCALE_SERVE_LOCAL_PORT}"
        /usr/local/bin/tailscale serve --https=443 "${TAILSCALE_SERVE_PROTOCOL}://localhost:${TAILSCALE_SERVE_LOCAL_PORT}"
    fi
else
    echo "TAILSCALE_SERVE_ENABLED is not 'true', skipping tailscale serve configuration."
fi

# Keep the script running indefinitely to prevent container exit.
exec tail -f /dev/null
