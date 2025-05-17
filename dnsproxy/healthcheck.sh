#!/bin/sh
set -euo pipefail

# Number of consecutive failures before attempting to kill dnsproxy
THRESHOLD=3
CNTFILE_PROCESS=/tmp/hc_process_fail_count
CNTFILE_NSLOOKUP=/tmp/hc_nslookup_fail_count

# Check if dnsproxy process is running
if ! pgrep -x dnsproxy > /dev/null; then
    echo "dnsproxy process not found."
    # Increment process failure counter
    count_process=$(cat "$CNTFILE_PROCESS" 2>/dev/null || echo 0)
    count_process=$((count_process + 1))
    echo "$count_process" > "$CNTFILE_PROCESS"

    if [ "$count_process" -ge "$THRESHOLD" ]; then
        echo "Health check failed $count_process times: dnsproxy not running. Attempting to kill dnsproxy (though it seems not to be running)."
        # Attempt to kill dnsproxy, though pgrep indicates it's not running.
        # This is more of a safeguard or for logging purposes.
        pkill -x -TERM dnsproxy || true
        sleep 2 # Give time for termination
        pkill -x -KILL dnsproxy || true
        rm -f "$CNTFILE_PROCESS" # Reset counter after action
        exit 1 # Mark unhealthy
    fi
    exit 1 # Mark unhealthy for this round
else
    # Reset process failure counter on success (if dnsproxy is running, this check passed)
    rm -f "$CNTFILE_PROCESS"
fi

# If HEALTHCHECK_PORT is set, perform nslookup
if [ -n "${HEALTHCHECK_PORT:-}" ]; then
    if nslookup -port="${HEALTHCHECK_PORT}" www.google.com 127.0.0.1 > /dev/null 2>&1; then
        rm -f "$CNTFILE_NSLOOKUP" # Reset nslookup failure counter on success
        exit 0 # Healthy
    else
        echo "nslookup check failed on port ${HEALTHCHECK_PORT}."
        # Increment nslookup failure counter
        count_nslookup=$(cat "$CNTFILE_NSLOOKUP" 2>/dev/null || echo 0)
        count_nslookup=$((count_nslookup + 1))
        echo "$count_nslookup" > "$CNTFILE_NSLOOKUP"

        if [ "$count_nslookup" -ge "$THRESHOLD" ]; then
            echo "Health check failed $count_nslookup times (nslookup). Sending SIGTERM to dnsproxy."
            pkill -x -TERM dnsproxy || true
            sleep 10 # Wait for a clean exit
            echo "Health check failed $count_nslookup times (nslookup). Sending SIGKILL to dnsproxy."
            pkill -x -KILL dnsproxy || true
            rm -f "$CNTFILE_NSLOOKUP" # Reset counter after action
            exit 1 # Mark unhealthy
        fi
        exit 1 # Mark unhealthy for this round
    fi
else
    # If HEALTHCHECK_PORT is not set, just checking the process was enough (and it passed above)
    # No specific counter for this path, as success is determined by process check alone.
    exit 0 # Healthy
fi