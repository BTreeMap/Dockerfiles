# Use the ARM64 version of the rdesktop image based on Debian with XFCE
FROM lscr.io/linuxserver/rdesktop:debian-xfce

# Set environment variables for temporary directory and Microsoft packages
ENV DOCKER_TMP_DIR="/tmp/.docker" 
ENV MS_PACKAGES_DEB_PATH="$DOCKER_TMP_DIR/packages-microsoft-prod.deb"

# Set the apt frontend to non-interactive mode for automated installs
ARG DEBIAN_FRONTEND=noninteractive

# Copy application files and s6-overlay config to container root.
COPY root/ /

# Create the temporary directory for Docker operations
RUN mkdir -p $DOCKER_TMP_DIR && \              
    # Update package lists for the apt package manager
    apt-get update && \
    # Backup the modified exo-open file
    cp /usr/bin/exo-open /usr/bin/exo-open.bak && \
    mv /usr/bin/exo-open-real /usr/bin/exo-open && \
    # Upgrade installed packages to their latest versions
    apt-get upgrade -y && \                      
    # Application tweaks to modify the behavior of the Chromium desktop entry
    # Reference: https://github.com/linuxserver/docker-rdesktop/blob/debian-xfce/Dockerfile
    echo "**** Application tweaks ****" && \
    sed -i 's#^Exec=.*#Exec=/usr/local/bin/wrapped-chromium#g' /usr/share/applications/chromium.desktop && \
    mv /usr/bin/exo-open /usr/bin/exo-open-real && \
    # Restore the modified exo-open file
    mv /usr/bin/exo-open.bak /usr/bin/exo-open && \
    # Remove the screensaver autostart to improve the desktop experience
    echo "**** XFCE tweaks ****" && \
    rm -f /etc/xdg/autostart/xscreensaver.desktop && \
    # Install required packages for functionality
    apt-get install -y \
        bind9-dnsutils \
        curl \
        git \
        iptables \
        iputils-ping \
        jq \
        lsb-release \
        vim \
        wget \
    && \ 
    # Download the Microsoft package for the current Debian release
    wget https://packages.microsoft.com/config/debian/$(lsb_release -rs)/packages-microsoft-prod.deb -O $MS_PACKAGES_DEB_PATH && \ 
    # Install the Microsoft package
    dpkg -i $MS_PACKAGES_DEB_PATH && \           
    # Remove the downloaded Microsoft package to save space
    rm $MS_PACKAGES_DEB_PATH && \                 
    # Update package lists again after adding the Microsoft package repository
    apt-get update && \                           
    # Ensure the man1 directory exists for manual pages
    mkdir -p /usr/share/man/man1 && \           
    # Install Microsoft OpenJDK 21 for Java applications
    apt-get install -y msopenjdk-21 && \
    # Download and install Tailscale using its installation script
    curl -fsSL https://tailscale.com/install.sh | sh && \
    # Clean up apt cache to reduce image size
    apt-get clean && \
    # Remove unnecessary files to reduce final image size
    rm -rf \
        /config/.cache \
        /var/lib/apt/lists/* \
        /var/tmp/* \
        /tmp/*

# Optionally set JAVA_HOME for Java applications
ENV JAVA_HOME=/usr/lib/jvm/msopenjdk-21         

# Environment variables for Tailscale
ENV TAILSCALE_ENABLED="true" \
    TAILSCALE_ACCEPT_DNS="false" \
    TAILSCALE_AUTO_UPDATE="false" \
    TS_BE_EXIT_NODE="false" \
    TAILSCALE_USE_WEBCLIENT="true"
