#!/bin/ash

# Exit the script immediately if any command fails.
set -e

echo "Starting entrypoint.sh..."

# Optional path to SSH keys directory
: "${SSH_KEY_DIR:=""}"

# Create the target SSH directory if it does not exist
echo "Checking existence of /root/.ssh directory..."
mkdir -p /root/.ssh/
echo "/root/.ssh directory ensured."

# If SSH_KEY_DIR is set, copy its contents to /root/.ssh/
if [ -n "$SSH_KEY_DIR" ] && [ -d "$SSH_KEY_DIR" ]; then
    echo "SSH_KEY_DIR is set and found: $SSH_KEY_DIR"
    echo "Copying SSH keys from $SSH_KEY_DIR to /root/.ssh/"

    # Copy the contents of the SSH key directory
    cp -r "$SSH_KEY_DIR/"* /root/.ssh/
    echo "SSH keys copied from $SSH_KEY_DIR to /root/.ssh/"
else
    echo "No SSH key directory specified or directory not found. Skipping SSH keys setup."
fi

# Set the correct permissions for the .ssh directory and its contents
echo "Setting permissions for /root/.ssh directory and its contents."
chmod 700 /root/.ssh               # Read, write, and execute permissions for the owner only
chmod 600 /root/.ssh/*             # Read and write permissions for the owner only

# Change ownership, ensuring that it is set to root
echo "Changing ownership of /root/.ssh to root:root."
chown -R root:root /root/.ssh      # Set ownership to root for the .ssh directory and its contents

# Retrieve the SSH connection parameters from environment variables
echo "Retrieving SSH connection parameters from environment variables..."
SSH_USER="${SSH_USER:-}"            # Default to empty if not specified
SSH_HOST="${SSH_HOST:-}"            # Default to empty if not specified
SSH_PORT="${SSH_PORT:-22}"           # Default to 22 if not specified

echo "SSH_USER: $SSH_USER"
echo "SSH_HOST: $SSH_HOST"
echo "SSH_PORT: $SSH_PORT"

# Construct the SSH command based on whether SSH_USER is supplied
if [ -n "$SSH_USER" ]; then
    SSH_COMMAND="${SSH_USER}@${SSH_HOST}"  # Use user@host format
    echo "SSH command constructed as: $SSH_COMMAND"
else
    SSH_COMMAND="${SSH_HOST}"               # Use host only if user is empty
    echo "SSH command constructed as: $SSH_COMMAND"
fi

# Command to establish an SSH connection with autossh
RETRY_INTERVAL=1
MAX_INTERVAL=32

echo "Starting autossh with retry logic..."
while true; do
    echo "Attempting to establish SSH connection with autossh..."
    
    # Start autossh and ignore its exit status
    AUTOSSH_POLL=60 autossh -M 58449 -o "StrictHostKeyChecking=no" -o "ServerAliveInterval=30" -o "ServerAliveCountMax=3" -N "${SSH_COMMAND}" -p "${SSH_PORT}" || true

    # If autossh exits, print an error message
    echo "Error: autossh has exited. Retrying in ${RETRY_INTERVAL} seconds..."
    
    # Wait for the current retry interval
    sleep $RETRY_INTERVAL
    
    # Increase the retry interval with exponential backoff, up to the maximum interval
    RETRY_INTERVAL=$((RETRY_INTERVAL * 2))
    if [ $RETRY_INTERVAL -gt $MAX_INTERVAL ]; then
        RETRY_INTERVAL=$MAX_INTERVAL
    fi
done

# Keep the script running indefinitely to prevent container exit.
echo "Entrypoint script complete, entering idle to keep container alive."
exec tail -f /dev/null
