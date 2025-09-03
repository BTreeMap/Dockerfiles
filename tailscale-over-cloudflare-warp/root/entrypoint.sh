#!/bin/bash

# Before running this script, ensure to bind /dev/net/tun:/dev/net/tun 
# to the container. Additionally, grant the following capabilities:
#   - net_admin: Allows the container to manage networking aspects.
#   - sys_module: Enables loading and unloading kernel modules.
# During the first run, you may need to set up the credentials for Tailscale 
# and Cloudflare WARP manually.

# Exit the script immediately if any command fails
set -e

# (Optional) Additional arguments to pass to the tailscaled command.
# Example: "--tun=userspace-networking"
TAILSCALED_EXTRA_ARGS="${TAILSCALED_EXTRA_ARGS:-}"

# Additional arguments to pass to the tailscale set command. 
# Default: "--advertise-exit-node --accept-dns=false --accept-routes=false --webclient"
TAILSCALE_EXTRA_ARGS="${TAILSCALE_EXTRA_ARGS:---advertise-exit-node --accept-dns=false --accept-routes=false --webclient}"

# Sleep / wait durations (customizable via environment variables; defaults preserve original behavior)
# Time to wait after starting tailscaled before applying settings
TAILSCALE_INIT_SLEEP="${TAILSCALE_INIT_SLEEP:-5}"
# Time to wait after 'tailscale up' to allow connection establishment
TAILSCALE_POST_UP_SLEEP="${TAILSCALE_POST_UP_SLEEP:-5}"
# Time to wait after starting warp-svc before running warp-cli commands
WARP_INIT_SLEEP="${WARP_INIT_SLEEP:-10}"

# Create the /run/dbus directory if it does not already exist
echo "Creating /run/dbus directory..."
mkdir -p /run/dbus

# Check for the existence of the D-Bus PID file and remove it if found
echo "Removing existing D-Bus PID file if it exists..."
if [ -f /run/dbus/pid ]; then
    rm /run/dbus/pid
fi

# Start the D-Bus daemon with the specified configuration file
echo "Starting the D-Bus daemon..."
dbus-daemon --config-file=/usr/share/dbus-1/system.conf

# Optimize Tailscale performance by configuring UDP generic receive offload (GRO)
echo "Optimizing Tailscale performance with UDP GRO settings..."
NETDEV=$(ip -o route get 8.8.8.8 | cut -f 5 -d " ")
ethtool -K $NETDEV rx-udp-gro-forwarding on rx-gro-list off || echo "Failed to set GRO options on $NETDEV, continuing..."

# Start the Tailscale daemon in the background
echo "Starting the Tailscale daemon with extra args: ${TAILSCALED_EXTRA_ARGS}"
tailscaled --statedir=/var/lib/tailscale ${TAILSCALED_EXTRA_ARGS} &

# Sleep for 5 seconds to allow Tailscale daemon to initialize
echo "Sleeping for ${TAILSCALE_INIT_SLEEP}s to allow the Tailscale daemon to initialize..."
sleep "${TAILSCALE_INIT_SLEEP}"

# Apply configuration settings with tailscale set.
echo "Setting Tailscale configuration with extra args: ${TAILSCALE_EXTRA_ARGS}"
tailscale set ${TAILSCALE_EXTRA_ARGS}

# Bring up the Tailscale connection.
echo "Connecting Tailscale..."
tailscale up

# Allow some time (5 seconds) for the Tailscale connection to establish
echo "Sleeping for ${TAILSCALE_POST_UP_SLEEP}s to allow Tailscale connection to establish..."
sleep "${TAILSCALE_POST_UP_SLEEP}"

# Start the Cloudflare WARP service daemon and accept the terms of service
echo "Starting the Cloudflare WARP service and accepting terms of service..."
warp-svc --accept-tos &

# Allow the daemons to initialize by sleeping for an additional 5 seconds
echo "Sleeping for ${WARP_INIT_SLEEP}s to allow WARP service to initialize..."
sleep "${WARP_INIT_SLEEP}"

# Disable qlog debugging for the Warp client to reduce logging verbosity
echo "Disabling qlog debugging for Warp client..."
su -c "warp-cli --accept-tos debug qlog disable" ubuntu

# Connect the Warp client to the service as user 'ubuntu'
echo "Connecting to the Warp service as user 'ubuntu'..."
su -c "warp-cli --accept-tos connect" ubuntu

# Execute an indefinite tail command to keep the container running
echo "Starting indefinite tail command to keep the container running..."
# The command was changed from 'exec tail' to 'tail' so that 'tail' does not run as PID 1.
# This allows the healthcheck script to terminate the container by killing the 'tail' process,
# causing the entrypoint script to exit naturally.
tail -f /dev/null -s 86400 || true
