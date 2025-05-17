#!/bin/sh
set -euo pipefail

# Number of consecutive failures before attempting to kill dnsproxy
THRESHOLD=3
CNTFILE=/tmp/hc_fail_count

# Check if dnsproxy process is running
if ! pgrep -x dnsproxy > /dev/null; then
    echo "dnsproxy process not found."
    # Increment failure counter
    count=$(cat "$CNTFILE" 2>/dev/null || echo 0)
    count=$((count + 1))
    echo "$count" > "$CNTFILE"

    if [ "$count" -ge "$THRESHOLD" ]; then
        echo "Health check failed $count times: dnsproxy not running. Attempting to kill dnsproxy (though it seems not to be running)."
        # Attempt to kill dnsproxy, though pgrep indicates it's not running.
        # This is more of a safeguard or for logging purposes.
        pkill -x -TERM dnsproxy || true
        sleep 2 # Give time for termination
        pkill -x -KILL dnsproxy || true
        rm -f "$CNTFILE" # Reset counter after action
        exit 1 # Mark unhealthy
    fi
    exit 1 # Mark unhealthy for this round
fi

# If HEALTHCHECK_PORT is set, perform nslookup
if [ -n "${HEALTHCHECK_PORT:-}" ]; then
    if nslookup -port="${HEALTHCHECK_PORT}" www.google.com 127.0.0.1 > /dev/null 2>&1; then
        rm -f "$CNTFILE" # Reset failure counter on success
        exit 0 # Healthy
    else
        echo "nslookup check failed on port ${HEALTHCHECK_PORT}."
        # Increment failure counter
        count=$(cat "$CNTFILE" 2>/dev/null || echo 0)
        count=$((count + 1))
        echo "$count" > "$CNTFILE"

        if [ "$count" -ge "$THRESHOLD" ]; then
            echo "Health check failed $count times (nslookup). Sending SIGTERM to dnsproxy."
            pkill -x -TERM dnsproxy || true
            sleep 10 # Wait for a clean exit
            echo "Health check failed $count times (nslookup). Sending SIGKILL to dnsproxy."
            pkill -x -KILL dnsproxy || true
            rm -f "$CNTFILE" # Reset counter after action
            exit 1 # Mark unhealthy
        fi
        exit 1 # Mark unhealthy for this round
    fi
else
    # If HEALTHCHECK_PORT is not set, just checking the process is enough
    rm -f "$CNTFILE" # Reset failure counter on success
    exit 0 # Healthy
fi