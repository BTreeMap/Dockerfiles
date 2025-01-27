#!/bin/sh

# Exit the script immediately if any command fails.
set -e

# Ensure the TAILSCALE_AUTHKEY environment variable is set.
if [ -z "$TAILSCALE_AUTHKEY" ]; then
    echo "[$(date)] Error: TAILSCALE_AUTHKEY environment variable is not set."
    exit 1
fi

# Optimize Tailscale performance by enabling UDP Generic Receive Offload (GRO).
echo "[$(date)] Optimizing network settings for Tailscale..."
NETDEV=$(ip -o route get 8.8.8.8 | grep -oP '(?<=dev\s)\w+')
if ethtool -K $NETDEV rx-udp-gro-forwarding on > /dev/null 2>&1; then
    echo "[$(date)] Network optimization on device '$NETDEV' succeeded."
else
    echo "[$(date)] Warning: Unable to set GRO on '$NETDEV'. Proceeding without optimization."
fi

# Start the Tailscale daemon in the background.
echo "[$(date)] Starting the Tailscale daemon..."
/app/tailscaled --state=mem: --tun=userspace-networking &
TAILSCALED_PID=$!

# Function to clean up background processes
cleanup() {
    echo "[$(date)] Cleaning up background processes..."
    kill $TAILSCALED_PID
    exit
}

# Trap signals to ensure cleanup
trap cleanup INT TERM

# Sleep briefly to allow the Tailscale daemon to initialize.
echo "[$(date)] Waiting for Tailscale daemon to initialize..."
sleep 5

# Bring up the Tailscale network interface.
echo "[$(date)] Bringing up Tailscale interface..."
if /app/tailscale up \
    --authkey="${TAILSCALE_AUTHKEY}" \
    --hostname="dns-query-container" \
    --accept-dns=false; then
    echo "[$(date)] Tailscale interface is up."
else
    echo "[$(date)] Error: Failed to bring up Tailscale interface."
    cleanup
fi

# Sleep briefly to allow the Tailscale connection to establish.
echo "[$(date)] Waiting for Tailscale connection to establish..."
sleep 5

# Start the DNS querying script.
echo "[$(date)] Starting DNS querying script..."
exec /app/dns_query.sh
