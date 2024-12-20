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
/usr/local/bin/tailscaled $TAILSCALED_ARGS &

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
    /usr/local/bin/tailscale set $TAILSCALE_SET_EXTRA_ARGS
fi

echo "Running tailscale up with arguments: $TAILSCALE_UP_ARGS"
/usr/local/bin/tailscale up $TAILSCALE_UP_ARGS

# Start AdGuard Home in the background
ADGUARD_ARGS=""

if [ "$ADGUARD_NO_CHECK_UPDATE" = "true" ]; then
    echo "Disabling AdGuard Home update checks."
    ADGUARD_ARGS="$ADGUARD_ARGS --no-check-update"
fi

if [ "$ADGUARD_VERBOSE" = "true" ]; then
    echo "Enabling AdGuard Home verbose output."
    ADGUARD_ARGS="$ADGUARD_ARGS --verbose"
fi

ADGUARD_ARGS="$ADGUARD_ARGS -c $ADGUARD_CONFIG_PATH -w $ADGUARD_WORK_DIR"

if [ -n "$ADGUARD_EXTRA_ARGS" ]; then
    echo "Adding extra AdGuard Home arguments: $ADGUARD_EXTRA_ARGS"
    ADGUARD_ARGS="$ADGUARD_ARGS $ADGUARD_EXTRA_ARGS"
fi

echo "Starting AdGuard Home with arguments: $ADGUARD_ARGS"
/usr/local/bin/AdGuardHome $ADGUARD_ARGS &

# Wait for AdGuard Home to start
sleep 5

# Configure Tailscale serve to proxy services
if [ "$TAILSCALE_SERVE_ENABLED" = "true" ]; then
    # Set default values if not already set
    : "${TAILSCALE_SERVE_LOCAL_PORT:=8443}"
    : "${TAILSCALE_SERVE_PROTOCOL:=https+insecure}"
    
    echo "Configuring Tailscale serve..."
    
    if [ -n "$TAILSCALE_SERVE_EXTRA_ARGS" ]; then
        echo "Using custom Tailscale serve arguments: $TAILSCALE_SERVE_EXTRA_ARGS"
        /usr/local/bin/tailscale serve $TAILSCALE_SERVE_EXTRA_ARGS &
    else
        echo "Serving local ${TAILSCALE_SERVE_PROTOCOL}://localhost:${TAILSCALE_SERVE_LOCAL_PORT} on Tailscale HTTPS port 443"
        /usr/local/bin/tailscale serve --https=443 "${TAILSCALE_SERVE_PROTOCOL}://localhost:${TAILSCALE_SERVE_LOCAL_PORT}" &
    fi

    # Wait for Tailscale serve to start
    sleep 10
else
    echo "TAILSCALE_SERVE_ENABLED is not 'true'; skipping Tailscale serve configuration."
fi

# Configure Tailscale funnel to expose services to the public Internet
if [ "$TAILSCALE_FUNNEL_ENABLED" = "true" ]; then
    # Set default values if not already set
    : "${TAILSCALE_FUNNEL_LOCAL_PORT:=53}"
    : "${TAILSCALE_FUNNEL_PUBLIC_PORT:=853}"
    : "${TAILSCALE_FUNNEL_PROTOCOL:=tls-terminated-tcp}"
    
    echo "Configuring Tailscale funnel..."
    
    if [ -n "$TAILSCALE_FUNNEL_EXTRA_ARGS" ]; then
        echo "Using custom Tailscale funnel arguments: $TAILSCALE_FUNNEL_EXTRA_ARGS"
        /usr/local/bin/tailscale funnel $TAILSCALE_FUNNEL_EXTRA_ARGS &
    else
        echo "Exposing local ${TAILSCALE_FUNNEL_PROTOCOL}://localhost:${TAILSCALE_FUNNEL_LOCAL_PORT} to public port ${TAILSCALE_FUNNEL_PUBLIC_PORT}"
        /usr/local/bin/tailscale funnel --${TAILSCALE_FUNNEL_PROTOCOL}=${TAILSCALE_FUNNEL_PUBLIC_PORT} "tcp://localhost:${TAILSCALE_FUNNEL_LOCAL_PORT}" &
    fi
else
    echo "TAILSCALE_FUNNEL_ENABLED is not 'true'; skipping Tailscale funnel configuration."
fi

echo "Entrypoint script completed. Waiting for background processes."

# Keep the script running indefinitely to prevent the container from exiting.
exec tail -f /dev/null
