# Use Alpine 3.20 as the base image.
FROM alpine:3.20

# Copy the entrypoint script into the image
COPY entrypoint.sh /entrypoint.sh

# Install the OpenSSH client from the main repository, then install autossh
# from the community repository. Clean up unnecessary files to reduce image size.
RUN apk add --no-cache openssh-client && \
    apk add --no-cache --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community autossh && \
    # Clean up the apk cache
    rm -rf /var/cache/apk/* && \
    # Remove the empty /root/.ssh directory if it exists
    rm -rf /root/.ssh && \
    # Make the entrypoint script executable
    chmod +x /entrypoint.sh

# Set environment variables for autossh
ENV AUTOSSH_POLL=60 \
    AUTOSSH_PORT=58449 \
    SSH_EXTRA_ARGS="-o 'StrictHostKeyChecking=no' -o 'ServerAliveInterval=30' -o 'ServerAliveCountMax=3' -N" \
    SSH_KEY_DIR=""

# Set the entrypoint command to the script
ENTRYPOINT ["/entrypoint.sh"]
