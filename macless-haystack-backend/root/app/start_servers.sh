#!/bin/bash

# Start mh_endpoint.py in the background
python endpoint/mh_endpoint.py &

# Start location_server.py
python endpoint/location_server.py
