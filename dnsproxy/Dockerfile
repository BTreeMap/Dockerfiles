# Base image - uses Alpine Linux for minimal size
FROM alpine:3.21 AS base

# Install required packages and prepare environment
RUN apk update && \
    apk upgrade && \
    apk add \
        ca-certificates \
        libcap \
        tini \
        tzdata \
    && \
    mkdir -p /opt/dnsproxy && \
    chown -R nobody: /opt/dnsproxy && \
    # Clean up to reduce image size
    rm -rf \
        /var/cache/apk/* \
        /tmp/* \
        /var/tmp/* \
        /usr/share/man \
        /usr/share/doc \
        /usr/share/licenses

# Copy dnsproxy binary and config from the official image
COPY \
    --chown=nobody:nogroup \
    --from=adguard/dnsproxy:latest \
    /opt/dnsproxy/dnsproxy \
    /opt/dnsproxy/config.yaml \
    /opt/dnsproxy/

# Copy healthcheck script
COPY --chown=nobody:nogroup healthcheck.sh /opt/dnsproxy/healthcheck.sh
RUN chmod +x /opt/dnsproxy/healthcheck.sh

# Allow dnsproxy to bind to privileged ports without running as root
RUN setcap 'cap_net_bind_service=+eip' /opt/dnsproxy/dnsproxy

# Final stage - use scratch for minimal size
FROM scratch

# Copy everything from the base stage
COPY --from=base / /

# Port explanations:
# 53     : TCP, UDP : DNS
# 80     : TCP      : HTTP
# 443    : TCP, UDP : HTTPS, DNS-over-HTTPS (incl. HTTP/3), DNSCrypt (main)
# 853    : TCP, UDP : DNS-over-TLS, DNS-over-QUIC
# 5443   : TCP, UDP : DNSCrypt (alt)
# 6060   : TCP      : HTTP (pprof)
EXPOSE 53/tcp 53/udp \
       80/tcp \
       443/tcp 443/udp \
       853/tcp 853/udp \
       5443/tcp 5443/udp \
       6060/tcp

# Set working directory
WORKDIR /opt/dnsproxy

# Define non-root user for better security
USER nobody:nogroup

# Set environment variables
ENV HEALTHCHECK_PORT=""

# Healthcheck: run every 30s, allow 5s for startup, 5s timeout
HEALTHCHECK \
    --interval=30s \
    --start-period=5s \
    --timeout=5s \
    CMD /opt/dnsproxy/healthcheck.sh

# Set entrypoint and default command
ENTRYPOINT ["/sbin/tini", "--", "/opt/dnsproxy/dnsproxy"]
CMD ["--config-path=/opt/dnsproxy/config.yaml"]
