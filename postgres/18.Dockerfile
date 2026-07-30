FROM postgres:18-alpine3.23

# Upgrade apk packages
RUN apk upgrade --no-cache
