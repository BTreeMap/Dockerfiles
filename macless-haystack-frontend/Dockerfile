# Stage 1: Builder - Download, verify, and extract the web application
FROM alpine AS builder

# Set build-time arguments for the download URL and SHA256 hash
ARG DOWNLOAD_URL=https://github.com/dchristl/macless-haystack/releases/download/v2.2.0/webapplication.zip
ARG SHA256_HASH=a8cbbfca625afd644ff295613efc195e8138032b59ec7c32b2a0aab901f5c9df

# Install dependencies, download, verify, and extract in a single RUN command to minimize layers
RUN apk add --no-cache curl unzip \
    && mkdir /app \
    && cd /app \
    && curl -L -o webapplication.zip "$DOWNLOAD_URL" \
    && echo "$SHA256_HASH  webapplication.zip" | sha256sum -c - \
    && unzip webapplication.zip \
    && rm webapplication.zip

# Stage 2: Runtime - Set up Nginx to serve the application
FROM nginx:alpine-slim

# Set environment variables (can be overridden at runtime)
ENV BACKEND_ENDPOINT=http://macless-haystack-backend:6176
ENV FRONTEND_DOMAIN=http://localhost:54049

# Copy the web application from the builder stage to the Nginx HTML directory
COPY --from=builder /app/web/ /usr/share/nginx/html/

# Copy the Nginx configuration template and the replacement script
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY replace.sh /docker-entrypoint.d/01-replace.sh

# Remove the default Nginx configuration and set executable permissions in a single RUN command
RUN rm /etc/nginx/conf.d/default.conf \
    && chmod +x /docker-entrypoint.d/01-replace.sh

# Expose port 54049
EXPOSE 54049

# Use the default entrypoint provided by nginx:alpine-slim
# No need to specify ENTRYPOINT or CMD as we're using the defaults
