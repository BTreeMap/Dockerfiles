FROM lscr.io/linuxserver/nextcloud:latest

RUN \
    # Upgrade apk packages
    apk update && \ 
    apk upgrade && \ 
    apk add \
        perl \
    && \
    # Clean up to reduce image size
    rm -rf \
        /tmp/* \
        /usr/share/doc \
        /usr/share/licenses \
        /usr/share/man \
        /var/cache/apk/* \
        /var/tmp/*

# Set the required environment variables
ENV PUID=1000 \
    PGID=1000 \
    TZ=America/Toronto
