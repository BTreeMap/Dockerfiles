#!/bin/bash

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
    # sends SIGTERM to PID 1, stopping the container
    kill 1
  fi
  # mark this check as failed
  exit 1
fi
