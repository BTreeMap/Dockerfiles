FROM --platform=linux/arm64 lscr.io/linuxserver/rdesktop:arm64v8-ubuntu-kde

# Set environment variables
ENV DOCKER_TMP_DIR="/tmp/.docker"
ENV MS_PACKAGES_DEB_PATH="$DOCKER_TMP_DIR/packages-microsoft-prod.deb"

# Create temporary directory and install necessary packages
RUN mkdir -p $DOCKER_TMP_DIR && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get upgrade -y && \
    echo "**** kde tweaks ****" && \
    sed -i \
    's/applications:org.kde.discover.desktop,/applications:org.kde.konsole.desktop,/g' \
    /usr/share/plasma/plasmoids/org.kde.plasma.taskmanager/contents/config/main.xml && \
    apt-get install -y wget lsb-release apt-transport-https git vim && \
    ubuntu_release=$(lsb_release -rs) && \
    wget https://packages.microsoft.com/config/ubuntu/${ubuntu_release}/packages-microsoft-prod.deb -O $MS_PACKAGES_DEB_PATH && \
    dpkg -i $MS_PACKAGES_DEB_PATH && \
    rm $MS_PACKAGES_DEB_PATH && \
    apt-get update && \
    mkdir -p /usr/share/man/man1 && \
    apt-get install -y msopenjdk-21 && \
    apt-get clean && \
    rm -rf \
        /config/.cache \
        /var/lib/apt/lists/* \
        /var/tmp/* \
        /tmp/*

# Optionally, set JAVA_HOME if needed
ENV JAVA_HOME=/usr/lib/jvm/msopenjdk-21
