#!/bin/bash

mkdir -p /app/endpoint/data/location-server/keys

python /app/endpoint/location_server_patcher.py

if python /app/endpoint/check_config.py ; then
    echo "Config file found. Starting both mh_endpoint.py and location_server.py."

    # Start mh_endpoint.py in the background
    python /app/endpoint/mh_endpoint.py &

    # Start location_server.py
    python /app/endpoint/location_server.py

else
    echo "Config file not found. Running mh_endpoint.py to register the device."

    # Run mh_endpoint.py to perform the initial setup
    python /app/endpoint/mh_endpoint.py

    echo "Initial setup complete. Please restart the container to start the location server."
fi
