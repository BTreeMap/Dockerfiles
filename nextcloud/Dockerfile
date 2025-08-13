FROM lscr.io/linuxserver/nextcloud:latest

# Upgrade apk packages
RUN apk upgrade --no-cache

# Set the required environment variables
ENV PUID=1000 \
    PGID=1000 \
    TZ=America/Toronto
