# Use the alpine:3.20 image as a base with ARM64 support
FROM --platform=linux/arm64 alpine:3.20

# Set environment variables for non-interactive installations
ENV PYTHONUNBUFFERED=1

# Copy the custom entrypoint script into the root directory of the image
COPY entrypoint.sh /entrypoint.sh

# Install dependencies for sshuttle, pip, and clean up cache
RUN apk add --no-cache \
      python3-dev \
      libffi-dev \
      openssl-dev \
      build-base \
      py3-pip \
      iptables \
      openssh && \
    pip3 install sshuttle --break-system-packages && \
    rm -rf /var/cache/apk/* && \
    chmod +x /entrypoint.sh

# Set the entrypoint to the custom script
ENTRYPOINT ["/entrypoint.sh"]
