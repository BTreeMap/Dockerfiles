#!/bin/ash

# Before running this script, ensure to bind /dev/net/tun:/dev/net/tun 
# to the container. Additionally, grant the following capabilities:
#   - net_admin: Allows the container to manage networking aspects.
#   - sys_module: Enables loading and unloading kernel modules.

# Exit the script immediately if any command fails.
set -e

# Check for required environment variables and provide defaults
: "${SSH_USER:=""}"               # SSH user (default empty)
: "${SSH_HOST:="sshserver"}"      # Default SSH host
: "${SSH_METHOD:=""}"              # Optional method for sshuttle (e.g., "tproxy")
: "${SSH_DNS:=""}"                 # If set, will enable DNS proxying
: "${SSH_EXCLUDE:=""}"             # Comma-separated list of addresses to exclude (e.g., "sshserver,sshserver:22")
: "${SSH_KEY_DIR:=""}"             # Optional path to SSH keys directory
: "${SSH_EXTRA_ARGS:=""}"          # Optional extra arguments for sshuttle
: "${SSH_LISTEN:=""}"              # Optional listening address and port for sshuttle
: "${SSH_SUBNETS:="0/0"}"          # Subnets to route over the VPN, defaulting to all IPv4

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

# Construct the base sshuttle command
if [ -n "$SSH_USER" ]; then
    SSH_COMMAND="sshuttle -r ${SSH_USER}@${SSH_HOST}"
else
    SSH_COMMAND="sshuttle -r ${SSH_HOST}"
fi

# Add the subnets to the SSH command, defaulting to all IPv4 (0/0)
SSH_COMMAND="$SSH_COMMAND ${SSH_SUBNETS}"

# Append the optional listening address and port if specified
if [ ! -z "$SSH_LISTEN" ]; then
    SSH_COMMAND="$SSH_COMMAND --listen $SSH_LISTEN"
fi

# Append any additional arguments provided in SSH_EXTRA_ARGS
if [ ! -z "$SSH_EXTRA_ARGS" ]; then
    SSH_COMMAND="$SSH_COMMAND $SSH_EXTRA_ARGS"
fi

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

# Sleep for 5 seconds to allow sshuttle to connect
echo "Sleeping for 5 seconds to allow sshuttle to connect..."
sleep 5

# Start the original Tailscale entrypoint
exec /usr/local/bin/containerboot

# Keep the script running indefinitely to prevent container exit.
exec tail -f /dev/null
