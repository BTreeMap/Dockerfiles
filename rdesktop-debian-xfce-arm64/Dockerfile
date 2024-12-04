FROM --platform=linux/arm64 lscr.io/linuxserver/rdesktop:arm64v8-debian-xfce

# Set environment variables
ENV DOCKER_TMP_DIR="/tmp/.docker"
ENV MS_PACKAGES_DEB_PATH="$DOCKER_TMP_DIR/packages-microsoft-prod.deb"

# Create temporary directory and install necessary packages
RUN mkdir -p $DOCKER_TMP_DIR && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive \
    apt-get upgrade -y && \
    # Application tweaks; See https://github.com/linuxserver/docker-rdesktop/blob/debian-xfce/Dockerfile
    sed -i \
    's#^Exec=.*#Exec=/usr/local/bin/wrapped-chromium#g' \
    /usr/share/applications/chromium.desktop && \
    apt-get install -y wget lsb-release git vim && \
    wget https://packages.microsoft.com/config/debian/$(lsb_release -rs)/packages-microsoft-prod.deb -O $MS_PACKAGES_DEB_PATH && \
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
