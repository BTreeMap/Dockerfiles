#!/bin/bash

# Create the /run/dbus directory if it does not already exist
mkdir -p /run/dbus

# Check if the D-Bus PID file exists; if it does, remove it
if [ -f /run/dbus/pid ]; then
    rm /run/dbus/pid
fi

# Start the D-Bus daemon with the specified configuration file
dbus-daemon --config-file=/usr/share/dbus-1/system.conf

# Start the warp service daemon with the accept terms of service option,
# running it in the foreground
warp-svc --accept-tos &

exec tail -f /dev/null
