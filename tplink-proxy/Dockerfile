FROM nginx:alpine-slim

# Environment variables for the main backend server (Port 39602)
ENV BACKEND_SERVER_39602="http://192.168.1.1:80"
ENV HOST_HEADER_39602="192.168.1.1"
ENV JS_INJECT_CODE_39602="var MULTI_DOMAIN_SUPPORT=true;"

# Environment variables for the alternate backend server (Port 26782)
ENV BACKEND_SERVER_26782="http://192.168.2.1:80"
ENV HOST_HEADER_26782="192.168.2.1"
ENV JS_INJECT_CODE_26782="\"192.168.2.1\""

# Copy root filesystem
COPY root/ /

RUN apk update --no-cache && \
    apk upgrade --no-cache && \
    rm -rf /var/cache/apk/* /tmp/* /var/tmp/* /usr/share/man /usr/share/doc /usr/share/licenses && \
    rm /etc/nginx/conf.d/default.conf

EXPOSE 39602
EXPOSE 26782
