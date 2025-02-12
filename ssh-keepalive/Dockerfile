# Use Alpine 3.20 as the base image for ARM64 architecture
FROM alpine:3.20

# Copy the entrypoint script into the image
COPY entrypoint.sh /entrypoint.sh

# Install OpenSSH client from the main repository,
# then install autossh from the community repository.
# Clean up unnecessary files to reduce image size.
RUN apk add --no-cache openssh-client && \
    apk add --no-cache --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community autossh && \
    # Clean up apk cache
    rm -rf /var/cache/apk/* && \
    # Remove the empty .ssh directory if it exists
    rm -rf /root/.ssh && \
    # Make the entrypoint script executable
    chmod +x /entrypoint.sh

# Set the entrypoint command to the script
ENTRYPOINT ["/entrypoint.sh"]
