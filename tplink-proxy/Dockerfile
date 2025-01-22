FROM nginx:alpine-slim

# Environment variables for the main backend server (Port 39602)
ENV BACKEND_SERVER="http://192.168.0.105:80"
ENV HOST_HEADER="192.168.0.105"
ENV JS_INJECT_CODE="var MULTI_DOMAIN_SUPPORT=true;"

# Environment variables for the alternate backend server (Port 26782)
ENV BACKEND_SERVER_ALT="http://192.168.2.1:80"
ENV HOST_HEADER_ALT="192.168.2.1"
ENV JS_INJECT_CODE_ALT="\"192.168.2.1\""

# Copy root filesystem
COPY root/ /

RUN apk update --no-cache && \
    apk upgrade --no-cache && \
    rm -rf /var/cache/apk/* /tmp/* /var/tmp/* /usr/share/man /usr/share/doc /usr/share/licenses && \
    rm /etc/nginx/conf.d/default.conf

EXPOSE 39602
EXPOSE 26782
