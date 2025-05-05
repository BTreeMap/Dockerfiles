# Multi-stage build for minimal container size
FROM alpine:3.21 AS downloader

# Set architecture and download parameters
ARG TARGETPLATFORM

# bbctl binary URLs and checksums
ARG BEEPER_BRIDGE_URL_AMD64=https://github.com/beeper/bridge-manager/releases/download/v0.13.0/bbctl-linux-amd64
ARG BEEPER_BRIDGE_URL_ARM64=https://github.com/beeper/bridge-manager/releases/download/v0.13.0/bbctl-linux-arm64
ARG BEEPER_BRIDGE_SHA256_AMD64=d2b53e348d2e7b8dcbcd2e0666315afabdad9baa04ff79b9033a83f0f922d857
ARG BEEPER_BRIDGE_SHA256_ARM64=46aabd8dd4a631b6373b128c10ea157dabcc35b316144e343eccf97c5e5fb825

# Install dependencies and download appropriate bbctl binary
RUN apk update && \
    apk upgrade && \
    apk add ca-certificates curl && \
    # Download architecture-specific bbctl binary
    case "$TARGETPLATFORM" in \
        "linux/amd64") curl -L $BEEPER_BRIDGE_URL_AMD64 -o /bin/bbctl \
        && echo "$BEEPER_BRIDGE_SHA256_AMD64  /bin/bbctl" | sha256sum -c - ;; \
        "linux/arm64") curl -L $BEEPER_BRIDGE_URL_ARM64 -o /bin/bbctl \
        && echo "$BEEPER_BRIDGE_SHA256_ARM64  /bin/bbctl" | sha256sum -c - ;; \
    esac && \
    chmod +x /bin/bbctl

# Import lottieconverter
FROM dock.mau.dev/tulir/lottieconverter:alpine-3.21 AS lottie

# Main builder stage
FROM alpine:3.21 AS builder

# Install dependencies
RUN apk update && apk upgrade && apk add \
    bash \
    curl \
    ffmpeg \
    git \
    jq \
    # Python for python bridges
    py3-pip \
    py3-setuptools \
    py3-wheel \
    python3 \
    # Common dependencies that need native extensions for Python bridges
    py3-aiohttp \
    py3-magic \
    py3-olm \
    py3-pillow \
    py3-pycryptodome \
    py3-ruamel.yaml

# Copy and install cryptg wheel
COPY --from=lottie /cryptg-*.whl /tmp/
RUN pip3 install --break-system-packages /tmp/cryptg-*.whl

# Copy artifacts from previous stages
COPY --from=downloader /bin/bbctl /usr/local/bin/bbctl
COPY --from=lottie /usr/lib/librlottie.so* /usr/lib/
COPY --from=lottie /usr/local/bin/lottieconverter /usr/local/bin/lottieconverter
COPY root/ /

# Clean up
RUN rm -rf /tmp/* && rm -rf /var/cache/apk/*

# Final minimal image
FROM scratch

# Copy all built content
COPY --from=builder / /

# Configuration
ENV BEEPER_ENV=prod \
    DATA_DIR=/data \
    DB_DIR=/data/db \
    MATRIX_ACCESS_TOKEN=01JQHEEC38R3DM3JBKD0DQRRSB \
    SYSTEM_SITE_PACKAGES=true

VOLUME /data

ENTRYPOINT ["/usr/local/bin/run-bridge.sh"]
