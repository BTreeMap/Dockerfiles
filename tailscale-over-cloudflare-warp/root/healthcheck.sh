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

# 3. On too many failures, send SIGTERM to all, wait, then SIGKILL
if (( count >= THRESHOLD )); then
  rm -f "$CNTFILE"
  echo "Health-check failed $count times: sending SIGTERM to all processes"
  kill -TERM -- -1 || true      # Graceful shutdown of every process
  sleep 10                      # Wait for clean exit
  echo "Forcing SIGKILL to all processes"
  kill -KILL -- -1 || true      # Forceful termination of any survivors
  exit 1                        # Mark unhealthy so Docker can restart
fi

# 4. Report unhealthy for this round
exit 1
