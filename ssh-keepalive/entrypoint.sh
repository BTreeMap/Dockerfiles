#!/bin/ash

# Exit the script immediately if any command fails.
set -e

# Optional path to SSH keys directory
: "${SSH_KEY_DIR:=""}"

# Create the target SSH directory if it does not exist
mkdir -p /root/.ssh/

# If SSH_KEY_DIR is set, copy its contents to /root/.ssh/
if [ -n "$SSH_KEY_DIR" ] && [ -d "$SSH_KEY_DIR" ]; then
    echo "Copying SSH keys from $SSH_KEY_DIR to /root/.ssh/"

    # Copy the contents of the SSH key directory
    cp -r "$SSH_KEY_DIR/"* /root/.ssh/
    echo "SSH keys copied from $SSH_KEY_DIR to /root/.ssh/"
else
    echo "No SSH key directory specified or directory not found. Skipping SSH keys setup."
fi

# Set the correct permissions for the .ssh directory and its contents
chmod 700 /root/.ssh               # Read, write, and execute permissions for the owner only
chmod 600 /root/.ssh/*             # Read and write permissions for the owner only

# Change ownership, ensuring that it is set to root
chown -R root:root /root/.ssh      # Set ownership to root for the .ssh directory and its contents

# Retrieve the SSH connection parameters from environment variables
SSH_USER="${SSH_USER:-}"            # Default to empty if not specified
SSH_HOST="${SSH_HOST:-}"            # Default to empty if not specified
SSH_PORT="${SSH_PORT:-22}"           # Default to 22 if not specified

# Construct the SSH command based on whether SSH_USER is supplied
if [ -n "$SSH_USER" ]; then
    SSH_COMMAND="${SSH_USER}@${SSH_HOST}"  # Use user@host format
else
    SSH_COMMAND="${SSH_HOST}"               # Use host only if user is empty
fi

# Command to establish an SSH connection with autossh
exec autossh -M 30038:32593 -o "StrictHostKeyChecking=no" -o "ServerAliveInterval=60" -o "ServerAliveCountMax=3" -N "${SSH_COMMAND}" -p "${SSH_PORT}" || { echo "Error: autossh failed to connect. Please check your SSH configuration and try again." && exit 1; }

# Keep the script running indefinitely to prevent container exit.
exec tail -f /dev/null
