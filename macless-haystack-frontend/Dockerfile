# Stage 1: Builder - Download, verify, and extract the web application
FROM alpine AS builder

# Set build-time arguments for the download URL and SHA256 hash
ARG DOWNLOAD_URL=https://github.com/dchristl/macless-haystack/releases/download/v2.2.0/webapplication.zip
ARG SHA256_HASH=a8cbbfca625afd644ff295613efc195e8138032b59ec7c32b2a0aab901f5c9df

# Install dependencies required for downloading and extracting
RUN apk add --no-cache curl unzip

# Set the working directory
WORKDIR /app

# Download the zip file
RUN curl -L -o webapplication.zip "$DOWNLOAD_URL"

# Verify the SHA256 hash
RUN echo "$SHA256_HASH  webapplication.zip" | sha256sum -c -

# Unzip the contents
RUN unzip webapplication.zip \
    && rm webapplication.zip

# Stage 2: Runtime - Set up Nginx to serve the application
FROM nginx:alpine

# Set environment variables (can be overridden at runtime)
ENV BACKEND_ENDPOINT=http://macless-haystack:6176

# Remove the default Nginx configuration
RUN rm /etc/nginx/conf.d/default.conf

# Create a directory for Nginx configuration templates
RUN mkdir -p /etc/nginx/templates

# Copy the Nginx configuration template into the container
COPY nginx.conf.template /etc/nginx/templates/nginx.conf.template

# Copy the entrypoint script into the container
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Copy the web application from the builder stage to the Nginx HTML directory
COPY --from=builder /app/web/ /usr/share/nginx/html/

# Expose port 54049
EXPOSE 54049

# Use the entrypoint script for dynamic configuration
ENTRYPOINT ["/entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
