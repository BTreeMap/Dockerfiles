#!/bin/sh
set -euo pipefail

# Number of consecutive failures before attempting to kill dnsproxy
THRESHOLD=3
EXECUTABLE_PATH=/opt/dnsproxy/dnsproxy
CNTFILE=/tmp/hc_nslookup_fail_count

# Check if dnsproxy process is running
if ! pgrep -x "$EXECUTABLE_PATH" > /dev/null; then
    echo "dnsproxy process not found."
    exit 1 # Mark unhealthy
fi

# If HEALTHCHECK_PORT is set, perform nslookup
if [ -n "${HEALTHCHECK_PORT:-}" ]; then
    if nslookup -port="${HEALTHCHECK_PORT}" www.google.com 127.0.0.1 > /dev/null 2>&1 \
        || nslookup -port="${HEALTHCHECK_PORT}" www.cloudflare.com 127.0.0.1 > /dev/null 2>&1 \
        || nslookup -port="${HEALTHCHECK_PORT}" www.microsoft.com 127.0.0.1 > /dev/null 2>&1; then
        rm -f "$CNTFILE" # Reset nslookup failure counter on success
        exit 0 # Healthy
    else
        echo "nslookup check failed on port ${HEALTHCHECK_PORT}."
        # Increment nslookup failure counter
        count=$(cat "$CNTFILE" 2>/dev/null || echo 0)
        count=$((count + 1))
        echo "$count" > "$CNTFILE"

        if [ "$count" -ge "$THRESHOLD" ]; then
            echo "Health check failed $count times (nslookup). Sending SIGTERM to dnsproxy."
            pkill -TERM -x "$EXECUTABLE_PATH" || true
            sleep 10 # Wait for a clean exit
            echo "Health check failed $count times (nslookup). Sending SIGKILL to dnsproxy."
            pkill -KILL -x "$EXECUTABLE_PATH" || true
            rm -f "$CNTFILE" # Reset counter after action
            exit 1 # Mark unhealthy
        fi
        exit 1 # Mark unhealthy for this round
    fi
else
    # If HEALTHCHECK_PORT is not set, just checking the process was enough (and it passed above)
    # No specific counter for this path, as success is determined by process check alone.
    exit 0 # Healthy
fi