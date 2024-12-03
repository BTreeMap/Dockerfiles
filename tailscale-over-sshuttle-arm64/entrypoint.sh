#!/bin/ash

# Before running this script, ensure to bind /dev/net/tun:/dev/net/tun 
# to the container. Additionally, grant the following capabilities:
#   - net_admin: Allows the container to manage networking aspects.
#   - sys_module: Enables loading and unloading kernel modules.

# Exit the script immediately if any command fails.
set -e

# Check for required environment variables and provide defaults
: "${SSH_USER:="username"}"     # Default SSH user
: "${SSH_HOST:="sshserver"}"     # Default SSH host
: "${SSH_METHOD:=""}"            # Optional method for sshuttle (e.g., "tproxy")
: "${SSH_DNS:=""}"               # If set, will enable DNS proxying
: "${SSH_EXCLUDE:=""}"           # Comma-separated list of addresses to exclude (e.g., "sshserver,sshserver:22")
: "${SSH_KEY_DIR:=""}"           # Optional path to ssh keys directory

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
chmod 700 /root/.ssh
chmod 600 /root/.ssh/*

# Change ownership, ensuring that it is set to root
chown -R root:root /root/.ssh

# Construct the base sshuttle command
SSH_COMMAND="sshuttle -r ${SSH_USER}@${SSH_HOST} 0/0"

# Add DNS option if specified
if [ ! -z "$SSH_DNS" ]; then
    SSH_COMMAND="$SSH_COMMAND --dns"
fi

# Add method option if specified
if [ ! -z "$SSH_METHOD" ]; then
    SSH_COMMAND="$SSH_COMMAND --method $SSH_METHOD"
fi

# Add exclude option if specified
if [ ! -z "$SSH_EXCLUDE" ]; then
    SSH_COMMAND="$SSH_COMMAND -x $SSH_EXCLUDE"
fi

# Start sshuttle in the background
$SSH_COMMAND &

# Start the original Tailscale entrypoint
exec /usr/local/bin/containerboot

# Keep the script running indefinitely to prevent container exit.
exec tail -f /dev/null
