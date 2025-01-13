#!/bin/sh

# Check if BACKEND_ENDPOINT is set; if not, use the default
BACKEND_ENDPOINT="${BACKEND_ENDPOINT:-http://localhost:6176}"

# Replace all occurrences of "http://localhost:6176" with the value of BACKEND_ENDPOINT
find /usr/share/nginx/html/ -type f -exec \
    sed -i "s|http://localhost:6176|${BACKEND_ENDPOINT}|g" {} +

echo "Backend endpoint set to ${BACKEND_ENDPOINT} in static files."
