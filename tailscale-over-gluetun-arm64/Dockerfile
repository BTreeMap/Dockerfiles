# Use the latest version of the gluetun image, targeting the ARM64 architecture.
FROM --platform=linux/arm64 qmcgaw/gluetun:latest

# Copy the custom entrypoint script into the container.
COPY entrypoint.sh /entrypoint.sh

# Update the package list, upgrade existing packages, and install necessary dependencies.
RUN apk update && \
    apk upgrade && \

    # Install Tailscale from the edge community repository without cache to reduce image size.
    apk add --no-cache --update --repository=https://dl-cdn.alpinelinux.org/alpine/edge/community tailscale && \

    # Install wget and ethtool, also without cache for image efficiency.
    apk add --no-cache --update -l wget ethtool && \

    # Install a specific version of OpenVPN (version 2.5) from the main repository.
    apk add --no-cache --update --repository=https://dl-cdn.alpinelinux.org/alpine/v3.17/main openvpn\~2.5 && \

    # Remove the existing OpenVPN binary to avoid conflicts.
    rm -rf /usr/sbin/openvpn2.5 && \

    # Rename the current OpenVPN binary for versioning.
    mv /usr/sbin/openvpn /usr/sbin/openvpn2.5 && \

    # Uninstall the default OpenVPN package to clean up.
    apk del openvpn && \

    # Reinstall OpenVPN and other necessary packages including CA certificates and iptables.
    apk add --no-cache --update openvpn ca-certificates iptables iptables-legacy tzdata && \

    # Remove any residual OpenVPN binary for the latest version.
    rm -rf /usr/sbin/openvpn2.6 && \

    # Rename the newly installed OpenVPN binary for versioning.
    mv /usr/sbin/openvpn /usr/sbin/openvpn2.6 && \

    # Clean up unnecessary files to reduce the final image size.
    rm -rf /var/cache/apk/* /etc/openvpn/*.sh /usr/lib/openvpn/plugins/openvpn-plugin-down-root.so && \

    # Remove the default OpenVPN user created during installation.
    deluser openvpn && \

    # Create a directory for Gluetun configurations or data.
    mkdir -p /gluetun && \

    # Ensure /gluetun is empty
    rm -rf /gluetun

# Set the entrypoint for the container to the specified script, allowing for proper initialization.
ENTRYPOINT ["/entrypoint.sh"]