#!/bin/bash
set -euo pipefail

# Number of consecutive failures before tearing down
THRESHOLD=3
CNTFILE=/tmp/hc_fail_count

# 1. Check WARP status as ubuntu user
if su - ubuntu -c 'warp-cli status' | grep -q 'Status update: Connected'; then
  rm -f "$CNTFILE"              # Reset failure counter on success
  exit 0
fi

# 2. Increment failure counter
count=$(<"$CNTFILE" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$CNTFILE"

# 3. On too many failures, send SIGTERM to all 'tail' processes, wait, then SIGKILL.
# The entrypoint uses 'tail' to keep the container running. Killing 'tail' will cause the entrypoint to exit, thereby terminating the container.
if (( count >= THRESHOLD )); then
  rm -f "$CNTFILE"
  echo "Health check failed $count times: sending SIGTERM to all 'tail' processes."
  pkill -x -TERM tail || true   # Gracefully shut down all 'tail' processes
  sleep 10                      # Wait for a clean exit
  echo "Health check failed $count times: sending SIGKILL to all remaining 'tail' processes."
  pkill -x -KILL tail || true   # Forcefully terminate any remaining 'tail' processes
  exit 1                        # Mark unhealthy so Docker can restart
fi

# 4. Report unhealthy for this round
exit 1
