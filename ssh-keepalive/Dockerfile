# Using Alpine 3.21 as the base image.
FROM alpine:3.21 AS base

# Copy the entrypoint script into the image
COPY entrypoint.sh /entrypoint.sh

# Install SSH tools, clean up cache, and prepare entrypoint script
RUN apk update && \
    apk upgrade && \
    apk add autossh openssh-client && \
    # Remove the empty /root/.ssh directory if it exists.
    rm -rf /root/.ssh && \
    # Make the entrypoint script executable.
    chmod +x /entrypoint.sh && \
    # Clean up the apk cache.
    rm -rf /var/cache/apk/*

FROM scratch

# Copy the base image contents to the final image
COPY --from=base / /

# Set environment variables for autossh
ENV AUTOSSH_POLL=60 \
    AUTOSSH_PORT=58449 \
    SSH_COMMAND="tail -f /dev/null -s 86400" \
    SSH_EXTRA_ARGS="-o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -N" \
    SSH_HOST="github.com" \
    SSH_KEY_DIR="" \
    SSH_PORT=22 \
    SSH_USER="git"

# Set the entrypoint command to the script
ENTRYPOINT ["/entrypoint.sh"]
