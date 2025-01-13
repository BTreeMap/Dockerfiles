#!/bin/sh

# Check if FRONTEND_DOMAIN is set; if not, use the default
FRONTEND_DOMAIN="${FRONTEND_DOMAIN:-http://localhost:54049}"

# Replace all occurrences of "http://localhost:6176" with "${FRONTEND_DOMAIN}/backend/"
find /usr/share/nginx/html/ -type f -exec \
    sed -i "s|http://localhost:6176|${FRONTEND_DOMAIN}/backend/|g" {} +

echo "Replaced 'http://localhost:6176' with '${FRONTEND_DOMAIN}/backend/' in static files."
