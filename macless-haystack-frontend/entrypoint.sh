#!/bin/sh

# Use envsubst to substitute environment variables in the Nginx config template
envsubst '${BACKEND_ENDPOINT}' < /etc/nginx/templates/nginx.conf.template > /etc/nginx/conf.d/default.conf

# Execute the command passed to the container (default: start Nginx)
exec "$@"
