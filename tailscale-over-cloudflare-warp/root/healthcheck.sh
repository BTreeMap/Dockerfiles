#!/bin/bash
set -euo pipefail

# Number of consecutive failures before tearing down
THRESHOLD=3
# Minimum time (in seconds) that must pass since first failure before terminating
MIN_FAIL_DURATION=60
CNTFILE=/tmp/hc_fail_count
FIRST_FAIL_TIME_FILE=/tmp/hc_first_fail_time

# 1. Check WARP status as ubuntu user
warp_status=$(su - ubuntu -c 'warp-cli status' 2>/dev/null || true)
case "$warp_status" in
  *"Status update: Connected"*)
    echo "WARP status check: Passed."
    rm -f "$CNTFILE"              # Reset failure counter on success
    rm -f "$FIRST_FAIL_TIME_FILE" # Reset first failure time on success
    exit 0
    ;;
esac

# 2. Increment failure counter
echo "WARP status check: Failed. Incrementing failure counter."

# Get current timestamp
current_time=$(date +%s)

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

# Store the first failure time if this is the first failure
if [ "$count" -eq 1 ]; then
  echo "$current_time" > "$FIRST_FAIL_TIME_FILE"
fi

# 3. On too many failures, send SIGTERM to all 'tail' processes, wait, then SIGKILL.
# The entrypoint uses 'tail' to keep the container running. Killing 'tail' will cause the entrypoint to exit, thereby terminating the container.
if [ "$count" -ge "$THRESHOLD" ]; then
  # Check if at least 30 seconds have passed since the first failure
  first_fail_time=0
  if [ -f "$FIRST_FAIL_TIME_FILE" ]; then
    first_fail_time=$(head -n 1 "$FIRST_FAIL_TIME_FILE" 2>/dev/null || echo "0")
    # Validate that first_fail_time is a number
    case "$first_fail_time" in
      ''|*[!0-9]*) first_fail_time=0 ;;
    esac
  fi
  
  time_since_first_fail=$((current_time - first_fail_time))
  
  if [ "$time_since_first_fail" -ge "$MIN_FAIL_DURATION" ]; then
    rm -f "$CNTFILE"
    rm -f "$FIRST_FAIL_TIME_FILE"
    echo "Health check failed $count times over $time_since_first_fail seconds. Sending SIGTERM to all 'tail' processes."
    pkill -x -TERM tail || true   # Gracefully shut down all 'tail' processes
    sleep 10                      # Wait for a clean exit
    echo "Health check failed $count times. Sending SIGKILL to all remaining 'tail' processes."
    pkill -x -KILL tail || true   # Forcefully terminate any remaining 'tail' processes
    exit 1                        # Mark unhealthy so Docker can restart
  else
    echo "Health check failed $count times but only $time_since_first_fail seconds since first failure (need $MIN_FAIL_DURATION). Not terminating yet."
  fi
fi

# 4. Report unhealthy for this round
echo "Health check: Failed for this round."
exit 1
