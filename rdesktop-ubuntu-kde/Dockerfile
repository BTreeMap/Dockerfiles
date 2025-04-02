# Use the ARM64 version of the rdesktop image based on Ubuntu with KDE
FROM lscr.io/linuxserver/rdesktop:ubuntu-kde

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
    # Upgrade installed packages to their latest versions
    apt-get upgrade -y && \                      
    # Indicate that KDE tweaks are being applied
    echo "**** KDE tweaks ****" && \              
    # Modify the Plasma task manager to prioritize the Konsole application
    # Reference: https://github.com/linuxserver/docker-rdesktop/blob/ubuntu-kde/Dockerfile
    sed -i 's/applications:org.kde.discover.desktop,/applications:org.kde.konsole.desktop,/g' \
    /usr/share/plasma/plasmoids/org.kde.plasma.taskmanager/contents/config/main.xml && \ 
    # Install essential packages required for the setup
    apt-get install -y \
        wget \
        curl \
        lsb-release \
        apt-transport-https \
        git \
        vim \
        jq \
        iptables \
        iputils-ping \
    && \ 
    # Get the current Ubuntu version to construct the Microsoft package URL
    ubuntu_release=$(lsb_release -rs) && \ 
    # Download the Microsoft package repository configuration for Ubuntu
    wget https://packages.microsoft.com/config/ubuntu/${ubuntu_release}/packages-microsoft-prod.deb -O $MS_PACKAGES_DEB_PATH && \ 
    # Install the downloaded Microsoft package
    dpkg -i $MS_PACKAGES_DEB_PATH && \           
    # Remove the downloaded Microsoft package to free up space
    rm $MS_PACKAGES_DEB_PATH && \                 
    # Update package lists again after adding the Microsoft repository
    apt-get update && \                           
    # Ensure the man1 directory exists for storing manual pages
    mkdir -p /usr/share/man/man1 && \           
    # Install Microsoft OpenJDK 21
    apt-get install -y msopenjdk-21 && \
    # Download and install Tailscale using its installation script
    curl -fsSL https://tailscale.com/install.sh | sh && \
    # Clean up the apt cache to reduce the final image size
    apt-get clean && \                            
    # Remove unnecessary files to minimize image size
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
