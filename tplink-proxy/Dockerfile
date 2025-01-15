FROM nginx:alpine-slim

ENV BACKEND_SERVER="http://192.168.0.105:80"
ENV HOST_HEADER="192.168.0.105"
ENV JS_INJECT_CODE="var MULTI_DOMAIN_SUPPORT=true;"

# Copy root filesystem
COPY root/ /

RUN apk update --no-cache && \
    apk upgrade --no-cache && \
    rm -rf /var/cache/apk/* /tmp/* /var/tmp/* /usr/share/man /usr/share/doc /usr/share/licenses && \
    rm /etc/nginx/conf.d/default.conf

EXPOSE 39602
