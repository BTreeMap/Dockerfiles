#!/bin/bash
set -euo pipefail

# How many failures before we bail
THRESHOLD=3
# Path to persistent failure counter
CNTFILE=/tmp/hc_fail_count

# Check WARP status as the ubuntu user
if su - ubuntu -c 'warp-cli status' | grep -q 'Status update: Connected'; then
  rm -f "$CNTFILE"
  exit 0
fi

# On failure, bump the counter
count=0
if [[ -f "$CNTFILE" ]]; then
  read -r count < "$CNTFILE" || count=0
fi
count=$((count + 1))
echo "$count" > "$CNTFILE"

# If we’ve failed enough times, reset and kill PID 1
if (( count >= THRESHOLD )); then
  rm -f "$CNTFILE"
  # graceful shutdown
  kill -TERM 1 || true
  # give it a moment
  sleep 10
  # force if still alive
  kill -KILL 1 || true
fi

# tell Docker “unhealthy” this round
exit 1
