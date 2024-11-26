# Create the /run/dbus directory if it does not already exist
sudo mkdir -p /run/dbus

# Check if the D-Bus PID file exists; if it does, remove it
if [ -f /run/dbus/pid ]; then
    sudo rm /run/dbus/pid
fi

# Start the D-Bus daemon with the specified configuration file
sudo dbus-daemon --config-file=/usr/share/dbus-1/system.conf

# Start the warp service daemon with the accept terms of service option,
# running it in the background
sudo warp-svc --accept-tos &
