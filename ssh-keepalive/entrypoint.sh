#!/bin/ash

# Exit the script immediately if any command fails.
set -e

log_msg() {
    echo "$(date +'%Y-%m-%d %H:%M:%S') [ENTRYPOINT] - $@"
}

log_msg "Starting entrypoint script..."

# Default poll interval for autossh
: "${AUTOSSH_POLL:=60}"

# Default monitoring port for autossh
: "${AUTOSSH_PORT:=58449}"

# Default SSH command to be executed
: "${SSH_COMMAND:=""}"

# Optional SSH extra arguments
: "${SSH_EXTRA_ARGS:=-N}"

# Optional path to SSH keys directory
: "${SSH_KEY_DIR:=""}"

# Log the environment variables
log_msg "AUTOSSH_POLL: $AUTOSSH_POLL"
log_msg "AUTOSSH_PORT: $AUTOSSH_PORT"
log_msg "SSH_COMMAND: $SSH_COMMAND"
log_msg "SSH_EXTRA_ARGS: $SSH_EXTRA_ARGS"
log_msg "SSH_KEY_DIR: $SSH_KEY_DIR"

# Create the target SSH directory if it does not exist
log_msg "Checking for existence of /root/.ssh directory..."
mkdir -p /root/.ssh/
log_msg "/root/.ssh directory ensured."

# If SSH_KEY_DIR is set, copy its contents to /root/.ssh/
if [ -n "$SSH_KEY_DIR" ] && [ -d "$SSH_KEY_DIR" ]; then
    log_msg "SSH_KEY_DIR is set and found: $SSH_KEY_DIR"
    log_msg "Copying SSH keys from $SSH_KEY_DIR to /root/.ssh/"

    # Copy the contents of the SSH key directory
    cp -r "$SSH_KEY_DIR/"* /root/.ssh/
    log_msg "SSH keys copied from $SSH_KEY_DIR to /root/.ssh/"
else
    log_msg "No SSH key directory specified or directory not found. Skipping SSH key setup."
fi

# Set the correct permissions for the .ssh directory and its contents
log_msg "Setting correct permissions for /root/.ssh and its contents."
chmod 700 /root/.ssh               # Read, write, and execute permissions for the owner only
chmod 600 /root/.ssh/*             # Read and write permissions for the owner only

# Change ownership, ensuring that it is set to root
log_msg "Changing ownership of /root/.ssh to root:root."
chown -R root:root /root/.ssh      # Set ownership to root for the .ssh directory and its contents

# Retrieve the SSH connection parameters from environment variables
log_msg "Retrieving SSH connection parameters from environment variables..."
SSH_USER="${SSH_USER:-}"            # Default to empty if not specified
SSH_HOST="${SSH_HOST:-}"            # Default to empty if not specified
SSH_PORT="${SSH_PORT:-22}"          # Default to 22 if not specified

log_msg "SSH_USER: $SSH_USER"
log_msg "SSH_HOST: $SSH_HOST"
log_msg "SSH_PORT: $SSH_PORT"

# Construct the SSH command based on whether SSH_USER is supplied
if [ -n "$SSH_USER" ]; then
    SSH_DESTINATION="${SSH_USER}@${SSH_HOST}"  # Use user@host format
    log_msg "SSH command constructed as: $SSH_DESTINATION"
else
    SSH_DESTINATION="${SSH_HOST}"               # Use host only if user is empty
    log_msg "SSH command constructed as: $SSH_DESTINATION"
fi

# Command to establish an SSH connection with autossh
RETRY_INTERVAL=1
MAX_INTERVAL=32

log_msg "Starting autossh with retry logic..."
while true; do
    log_msg "Attempting to establish an SSH connection with autossh..."
    autossh -M "${AUTOSSH_PORT}" ${SSH_EXTRA_ARGS} -p "${SSH_PORT}" "${SSH_DESTINATION}" -- ${SSH_COMMAND} || true

    # If autossh exits, print an error message
    log_msg "Error: autossh exited. Retrying in ${RETRY_INTERVAL} seconds..."
    
    # Wait for the current retry interval
    sleep $RETRY_INTERVAL
    
    # Increase the retry interval with exponential backoff, up to the maximum interval
    RETRY_INTERVAL=$((RETRY_INTERVAL * 2))
    if [ $RETRY_INTERVAL -gt $MAX_INTERVAL ]; then
        RETRY_INTERVAL=$MAX_INTERVAL
    fi
done

# Keep the script running indefinitely to prevent container exit.
log_msg "Entrypoint script complete, entering idle mode to keep the container alive."
exec tail -f /dev/null
