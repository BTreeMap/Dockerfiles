########################################
# 1. Build stage: Alpine base, upgraded
########################################
FROM alpine:3.21 AS base

# Upgrade all packages and install minimal debug tools
RUN apk update \
    && apk upgrade \
    && apk add \
        bind-tools \
        ca-certificates \
        curl \
        iproute2 \
        iputils-ping \
    && \
    rm -rf \
        /var/cache/apk/* \
        /tmp/* \
        /var/tmp/* \
        /usr/share/man \
        /usr/share/doc \
        /usr/share/licenses

########################################
# 2. Final stage: minimal scratch image
########################################
FROM scratch

# Copy entire filesystem from base image
COPY --from=base / /

# Use least-privilege: run as nobody user
USER nobody:nogroup

# Minimal entrypoint to keep the container running
ENTRYPOINT ["tail", "-f", "/dev/null", "-s", "86400"]
