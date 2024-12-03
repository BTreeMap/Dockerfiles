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
: "${SSH_METHOD:=""}"             # Optional method for sshuttle (e.g., "tproxy")
: "${SSH_DNS:=""}"                # If set, will enable DNS proxying
: "${SSH_EXCLUDE:=""}"            # Comma-separated list of addresses to exclude (e.g., "sshserver,sshserver:22")

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
