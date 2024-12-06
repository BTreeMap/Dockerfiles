#!/bin/ash

# Before running this script, ensure to bind /dev/net/tun:/dev/net/tun 
# to the container. Additionally, grant the following capabilities:
#   - net_admin: Allows the container to manage networking aspects.
#   - sys_module: Enables loading and unloading kernel modules.

# Exit the script immediately if any command fails.
set -e

# Optional path to a read-only server list
: "${INITIAL_SERVERS_PATH:=""}"

# If the environment variable INITIAL_SERVERS_PATH is set,
# create the /gluetun directory and copy the initial servers file.
if [ -n "$INITIAL_SERVERS_PATH" ]; then
  echo "Initializing server list from $INITIAL_SERVERS_PATH..."
  mkdir -p /gluetun
  cp "$INITIAL_SERVERS_PATH" /gluetun/servers.json
fi

# Optimize Tailscale performance by enabling UDP Generic Receive Offload (GRO).
echo "Optimizing Tailscale performance with UDP GRO settings..."
# Get the network device used for the default route.
NETDEV=$(ip -o route get 8.8.8.8 | cut -f 5 -d " ")
# Enable UDP GRO forwarding and disable GRO for other protocols.
ethtool -K $NETDEV rx-udp-gro-forwarding on rx-gro-list off

# Start the Tailscale daemon in the background.
echo "Starting the Tailscale daemon..."
/app/tailscaled --statedir=/var/lib/tailscale &

# Sleep for 5 seconds to allow the Tailscale daemon to initialize.
echo "Sleeping for 5 seconds to allow the Tailscale daemon to initialize..."
sleep 5

# Bring up the Tailscale connection and advertise this node as an exit node.
echo "Connecting Tailscale and advertising this node as an exit node..."
/app/tailscale set --advertise-exit-node --accept-dns=false --webclient
/app/tailscale up

# Allow some time (5 seconds) for the Tailscale connection to establish.
echo "Sleeping for 5 seconds to allow Tailscale connection to establish..."
sleep 5

# Start the Gluetun entrypoint script in the background.
./gluetun-entrypoint &

# Keep the script running indefinitely to prevent container exit.
exec tail -f /dev/null
