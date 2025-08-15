#!/bin/bash
set -euo pipefail

# Number of consecutive failures before tearing down
THRESHOLD=3
CNTFILE=/tmp/hc_fail_count

# 1. Check WARP status as ubuntu user
if su - ubuntu -c 'warp-cli status' | grep -q 'Status update: Connected'; then
  echo "WARP status check: Passed."
  rm -f "$CNTFILE"              # Reset failure counter on success
  exit 0
fi

# 2. Increment failure counter
echo "WARP status check: Failed. Incrementing failure counter."

# Initialize count variable
count=0 # Default to 0

# If the file exists, attempt to read its content.
if [ -f "$CNTFILE" ]; then
  # Read the first line of the file more efficiently
  file_content=$(head -n 1 "$CNTFILE" 2>/dev/null || echo "0")

  # Robustly check the content of file_content.
  # If it's a valid number, assign it to count. Otherwise, count remains 0.
  case "$file_content" in
    ''|*[!0-9]*) # If empty or contains non-digits, count remains 0
      # No action needed, count is already 0
      ;;
    *) # Otherwise, it's a number, assign it to count
      count="$file_content"
      ;;
  esac
fi

# Increment count using POSIX 'expr'
count=$(expr "$count" + 1)

# Write the new count back to the file atomically
echo "$count" > "$CNTFILE"

# 3. On too many failures, send SIGTERM to all 'tail' processes, wait, then SIGKILL.
# The entrypoint uses 'tail' to keep the container running. Killing 'tail' will cause the entrypoint to exit, thereby terminating the container.
if [ "$count" -ge "$THRESHOLD" ]; then
  rm -f "$CNTFILE"
  echo "Health check failed $count times. Sending SIGTERM to all 'tail' processes."
  pkill -x -TERM tail || true   # Gracefully shut down all 'tail' processes
  sleep 10                      # Wait for a clean exit
  echo "Health check failed $count times. Sending SIGKILL to all remaining 'tail' processes."
  pkill -x -KILL tail || true   # Forcefully terminate any remaining 'tail' processes
  exit 1                        # Mark unhealthy so Docker can restart
fi

# 4. Report unhealthy for this round
echo "Health check: Failed for this round."
exit 1
