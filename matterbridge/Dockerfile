# Use multi-stage build to keep final image size small.
FROM alpine:latest AS builder

# Install CA certificates and curl (for retrieving the binary and verifying the checksum).
ARG TARGETPLATFORM

ARG MATTERBRIDGE_URL_AMD64=https://github.com/42wim/matterbridge/releases/download/v1.26.0/matterbridge-1.26.0-linux-64bit
ARG MATTERBRIDGE_URL_ARM64=https://github.com/42wim/matterbridge/releases/download/v1.26.0/matterbridge-1.26.0-linux-arm64

ARG MATTERBRIDGE_SHA256_AMD64=7f5a74b9b7cbf395b8873f3f3f418d90c61197e3cc677882e70b7faeef4c35b0
ARG MATTERBRIDGE_SHA256_ARM64=3d922187ce3e792f9cf896e209c828d3466e3b2453f3a133f7f610f9d6937587

RUN apk update && \
    apk upgrade && \
    apk add --no-cache \
        ca-certificates \
        curl \
    && \
    # Check architecture and download correct matterbridge binary.
    case "$TARGETPLATFORM" in \
        "linux/amd64") curl -L $MATTERBRIDGE_URL_AMD64 -o /bin/matterbridge \
        && echo "$MATTERBRIDGE_SHA256_AMD64  /bin/matterbridge" | sha256sum -c - ;; \
        "linux/arm64") curl -L $MATTERBRIDGE_URL_ARM64 -o /bin/matterbridge \
        && echo "$MATTERBRIDGE_SHA256_ARM64  /bin/matterbridge" | sha256sum -c - ;; \
    esac

RUN chmod +x /bin/matterbridge

FROM scratch

# Copy certificates so matterbridge can make secure connections.
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=builder /bin/matterbridge /bin/matterbridge

ENTRYPOINT ["/bin/matterbridge", "-conf", "/conf/matterbridge.toml"]
