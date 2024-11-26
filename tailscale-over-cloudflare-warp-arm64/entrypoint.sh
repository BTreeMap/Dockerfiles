#!/bin/bash

# Create the /run/dbus directory if it does not already exist
mkdir -p /run/dbus

# Check if the D-Bus PID file exists; if it does, remove it
# This ensures we can start a new D-Bus session without conflicts
if [ -f /run/dbus/pid ]; then
    rm /run/dbus/pid
fi

# Start the D-Bus daemon using the specified configuration file
# This daemon is responsible for inter-process communication
dbus-daemon --config-file=/usr/share/dbus-1/system.conf

# Start the Warp service daemon and automatically accept the terms of service
# The "&" runs it in the background to allow the script to continue executing
warp-svc --accept-tos &

# Start the Tailscale daemon and specify the state file for persistent connection management
tailscaled --state=tailscaled.state &

# Disable qlog debugging for Warp client to minimize logging verbosity
warp-cli debug qlog disable

NETDEV=$(ip -o route get 8.8.8.8 | cut -f 5 -d " ")
sudo ethtool -K $NETDEV rx-udp-gro-forwarding on rx-gro-list off

# Execute an indefinite tail command to keep the container running
# This prevents the container from exiting, allowing the background services to continue
exec tail -f /dev/null
