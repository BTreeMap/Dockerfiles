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
mkdir -p /run/dbus

# Check for the existence of the D-Bus PID file and remove it if found
# This ensures a clean start for the D-Bus session without any conflicts
if [ -f /run/dbus/pid ]; then
    rm /run/dbus/pid
fi

# Start the D-Bus daemon with the specified configuration file
# This daemon facilitates inter-process communication for services
dbus-daemon --config-file=/usr/share/dbus-1/system.conf

# Start the Tailscale daemon, specifying the state file for persistent 
# connection management, and run it in the background
tailscaled --state=tailscaled.state &

# Start the Cloudflare WARP service daemon and accept the terms of service
# Running this in the background allows the script to continue executing
warp-svc --accept-tos &

# Allow the daemons to initialize by sleeping for 5 seconds
sleep 5

# Disable qlog debugging for the Warp client to reduce logging verbosity
warp-cli debug qlog disable

# Optimize Tailscale performance by configuring UDP generic receive offload (GRO)
# Find the active network device by checking the route to an external address (e.g., Google DNS)
NETDEV=$(ip -o route get 8.8.8.8 | cut -f 5 -d " ")
# Enable UDP GRO forwarding while disabling regular GRO for performance enhancement
sudo ethtool -K $NETDEV rx-udp-gro-forwarding on rx-gro-list off

# Bring up the Tailscale connection and advertise this node as an exit node
tailscale up --advertise-exit-node

# Allow some time (5 seconds) for the Tailscale connection to establish
sleep 5

# Connect the Warp client to the service
warp-cli connect

# Execute an indefinite tail command to keep the container running
# This prevents the container from exiting, allowing all background services to continue
exec tail -f /dev/null
