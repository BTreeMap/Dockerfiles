FROM --platform=linux/arm64 qmcgaw/gluetun:latest

RUN apk update && \
    apk upgrade && \
    apk add --no-cache --update --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community tailscale && \
    apk add --no-cache --update -l wget && \
    apk add --no-cache --update --repository=https://dl-cdn.alpinelinux.org/alpine/v3.17/main openvpn\~2.5 && \
    rm -rf /usr/sbin/openvpn2.5 && \
    mv /usr/sbin/openvpn /usr/sbin/openvpn2.5 && \
    apk del openvpn && \
    apk add --no-cache --update openvpn ca-certificates iptables iptables-legacy tzdata && \
    rm -rf /usr/sbin/openvpn2.6 && \
    mv /usr/sbin/openvpn /usr/sbin/openvpn2.6 && \
    rm -rf /var/cache/apk/* /etc/openvpn/*.sh /usr/lib/openvpn/plugins/openvpn-plugin-down-root.so && \
    deluser openvpn && \
    mkdir -p /gluetun
