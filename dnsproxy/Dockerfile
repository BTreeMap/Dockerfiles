# A docker file for scripts/make/build-docker.sh.

FROM alpine:3.21 AS base

# Update certificates.
RUN apk update && \
    apk upgrade && \
    apk add \
        ca-certificates \
        libcap \
        tzdata \
    && \
	mkdir -p /opt/dnsproxy && \
    chown -R nobody: /opt/dnsproxy && \
    rm -rf \
        /var/cache/apk/* \
        /tmp/* \
        /var/tmp/* \
        /usr/share/man \
        /usr/share/doc \
        /usr/share/licenses


COPY \
    --chown=nobody:nogroup \
	--from=adguard/dnsproxy:latest \
	/opt/dnsproxy/dnsproxy \
    /opt/dnsproxy/config.yaml \
    /opt/dnsproxy/

RUN setcap 'cap_net_bind_service=+eip' /opt/dnsproxy/dnsproxy

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

WORKDIR /opt/dnsproxy

ENTRYPOINT ["/opt/dnsproxy/dnsproxy"]
CMD ["--config-path=/opt/dnsproxy/config.yaml"]
