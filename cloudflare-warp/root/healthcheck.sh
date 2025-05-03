#!/usr/bin/env bash
# File: /healthcheck.sh

# Path to failure counter
CNTFILE=/tmp/hc_fail_count

# Run the real check
if warp-cli status | grep -q "Status update: Connected"; then
  rm -f "$CNTFILE"
  exit 0
else
  # Increment failure count
  count=$(cat "$CNTFILE" 2>/dev/null || echo 0)
  count=$((count + 1))
  echo "$count" > "$CNTFILE"
  if [ "$count" -ge 3 ]; then
    rm -f "$CNTFILE"
    kill 1   # sends SIGTERM to PID 1, stopping the container
  fi
  exit 1    # mark this check as failed
fi
