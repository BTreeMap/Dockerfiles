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

echo "Starting entrypoint script..."

# Start tailscaled with appropriate options
TAILSCALED_ARGS=""

if [ -n "$TAILSCALED_CUSTOM_STATE_DIR" ]; then
    TAILSCALED_STATE_DIR="$TAILSCALED_CUSTOM_STATE_DIR"
else
    TAILSCALED_STATE_DIR="/var/lib/tailscale"
fi

TAILSCALED_ARGS="$TAILSCALED_ARGS --statedir=$TAILSCALED_STATE_DIR"

if check_tun_device; then
    echo "TUN device is available."
else
    echo "TUN device is not available, using userspace networking."
    TAILSCALED_ARGS="$TAILSCALED_ARGS --tun=userspace-networking"
fi

if [ -n "$TAILSCALED_EXTRA_ARGS" ]; then
    echo "Adding extra tailscaled arguments: $TAILSCALED_EXTRA_ARGS"
    TAILSCALED_ARGS="$TAILSCALED_ARGS $TAILSCALED_EXTRA_ARGS"
fi

echo "Starting tailscaled with arguments: $TAILSCALED_ARGS"
/app/tailscaled $TAILSCALED_ARGS &

# Wait for tailscaled to start
sleep 5

# Construct tailscale up arguments
TAILSCALE_UP_ARGS="--accept-dns=false"

if [ -n "$TAILSCALE_AUTH_KEY" ]; then
    echo "Using Tailscale auth key."
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS --authkey=${TAILSCALE_AUTH_KEY}"
fi

if [ -n "$TAILSCALE_HOSTNAME" ]; then
    echo "Setting Tailscale hostname to $TAILSCALE_HOSTNAME"
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS --hostname=${TAILSCALE_HOSTNAME}"
fi

if [ -n "$TAILSCALE_EXTRA_ARGS" ]; then
    echo "Adding extra tailscale up arguments: $TAILSCALE_EXTRA_ARGS"
    TAILSCALE_UP_ARGS="$TAILSCALE_UP_ARGS ${TAILSCALE_EXTRA_ARGS}"
fi

# Run tailscale set with extra arguments
if [ -n "$TAILSCALE_SET_EXTRA_ARGS" ]; then
    echo "Running tailscale set with arguments: $TAILSCALE_SET_EXTRA_ARGS"
    /app/tailscale set $TAILSCALE_SET_EXTRA_ARGS
fi

echo "Running tailscale up with arguments: $TAILSCALE_UP_ARGS"
/app/tailscale up $TAILSCALE_UP_ARGS

# Start the DNS querying script.
echo "[$(date)] Starting DNS querying script..."
exec /app/dns_query.sh

# Keep the script running indefinitely to prevent the container from exiting.
exec tail -f /dev/null
