#!/bin/bash

# Before running this script, ensure to bind /dev/net/tun:/dev/net/tun 
# to the container. Additionally, grant the following capabilities:
#   - net_admin: Allows the container to manage networking aspects.
#   - sys_module: Enables loading and unloading kernel modules.
# During the first run, you may need to set up the credentials for Tailscale 
# and Cloudflare WARP manually.

# Exit the script immediately if any command fails
set -e

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
ethtool -K $NETDEV rx-udp-gro-forwarding on rx-gro-list off

# Start the Tailscale daemon in the background
echo "Starting the Tailscale daemon..."
tailscaled --statedir=/var/lib/tailscale --state=tailscaled.state &

# Sleep for 5 seconds to allow Tailscale daemon to initialize
echo "Sleeping for 5 seconds to allow the Tailscale daemon to initialize..."
sleep 5

# Bring up the Tailscale connection and advertise this node as an exit node
echo "Connecting Tailscale and advertising this node as an exit node..."
tailscale up --advertise-exit-node

# Allow some time (5 seconds) for the Tailscale connection to establish
echo "Sleeping for 5 seconds to allow Tailscale connection to establish..."
sleep 5

# Start the Cloudflare WARP service daemon and accept the terms of service
echo "Starting the Cloudflare WARP service and accepting terms of service..."
warp-svc --accept-tos &

# Allow the daemons to initialize by sleeping for an additional 5 seconds
echo "Sleeping for 5 seconds to allow WARP service to initialize..."
sleep 5

# Disable qlog debugging for the Warp client to reduce logging verbosity
echo "Disabling qlog debugging for Warp client..."
su -c "warp-cli --accept-tos debug qlog disable" ubuntu

# Connect the Warp client to the service as user 'ubuntu'
echo "Connecting to the Warp service as user 'ubuntu'..."
su -c "warp-cli --accept-tos connect" ubuntu

# Execute an indefinite tail command to keep the container running
echo "Starting indefinite tail command to keep the container running..."
exec tail -f /dev/null
