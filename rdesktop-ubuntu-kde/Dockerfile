# Use the rdesktop image based on Ubuntu with KDE (multi-arch: amd64 + arm64)
FROM lscr.io/linuxserver/rdesktop:ubuntu-kde


# Set the apt frontend to non-interactive mode for automated installs
ARG DEBIAN_FRONTEND=noninteractive

# Copy application files and s6-overlay config to container root.
COPY root/ /

# Create the temporary directory for Docker operations
RUN DOCKER_TMP_DIR="/tmp/.docker" && \
    MS_PACKAGES_DEB_PATH="$DOCKER_TMP_DIR/packages-microsoft-prod.deb" && \
    mkdir -p "$DOCKER_TMP_DIR" && \
    \
    # Update package lists for the apt package manager
    apt-get update && \
    \
    # Upgrade installed packages to their latest versions
    apt-get upgrade -y && \
    \
    # Indicate that KDE tweaks are being applied
    echo "**** KDE tweaks ****" && \
    \
    # Modify the Plasma task manager to prioritize the Konsole application
    # Reference: https://github.com/linuxserver/docker-rdesktop/blob/ubuntu-kde/Dockerfile
    sed -i 's/applications:org.kde.discover.desktop,/applications:org.kde.konsole.desktop,/g' \
      /usr/share/plasma/plasmoids/org.kde.plasma.taskmanager/contents/config/main.xml && \
    \
    # Install essential packages required for the setup
    apt-get install -y \
        bind9-dnsutils \
        ca-certificates \
        ca-certificates-java \
        curl \
        git \
        iproute2 \
        iptables \
        iputils-ping \
        jq \
        lsb-release \
        mtr \
        nano \
        okular \
        python-is-python3 \
        screen \
        unzip \
        vim \
        wget \
        xz-utils \
    && \
    \
    # Get the current Ubuntu version to construct the Microsoft package URL
    ubuntu_release=$(lsb_release -rs) && \
    \
    # Download the Microsoft package repository configuration for Ubuntu
    wget "https://packages.microsoft.com/config/ubuntu/${ubuntu_release}/packages-microsoft-prod.deb" -O "$MS_PACKAGES_DEB_PATH" && \
    \
    # Install the downloaded Microsoft package
    dpkg -i "$MS_PACKAGES_DEB_PATH" && \
    \
    # Remove the downloaded Microsoft package to free up space
    rm "$MS_PACKAGES_DEB_PATH" && \
    \
    # Update package lists again after adding the Microsoft repository
    apt-get update && \
    \
    # Ensure the man1 directory exists for storing manual pages
    mkdir -p /usr/share/man/man1 && \
    \
    # Install Microsoft OpenJDK 25
    apt-get install -y msopenjdk-25 && \
    # Install OpenJDK 8
    apt-get install -y openjdk-8-jre && \
    \
    # Install InCommon cert system-wide (download -> sha256 verify -> DER->PEM -> trust store)
    wget -O /tmp/InCommon.der https://s.joefang.org/InCommon.crt && \
    echo "87e01cc4dd0c9d92a3dbd49092ff13f9cd387445cdc57e5b984e1b7721b5b029  /tmp/InCommon.der" | sha256sum -c - && \
    openssl x509 -inform DER -outform PEM -in /tmp/InCommon.der -out /usr/local/share/ca-certificates/InCommon.crt && \
    update-ca-certificates -f && \
    \
    # Helper: point a given Java home at the system Java truststore
    # Handles both layouts:
    #   $JAVA_HOME/lib/security/cacerts
    #   $JAVA_HOME/jre/lib/security/cacerts
    link_java_cacerts() { \
      _home="$1"; \
      _target="/etc/ssl/certs/java/cacerts"; \
      if [ -e "${_home}/lib/security/cacerts" ]; then \
        _cacerts="${_home}/lib/security/cacerts"; \
      elif [ -e "${_home}/jre/lib/security/cacerts" ]; then \
        _cacerts="${_home}/jre/lib/security/cacerts"; \
      else \
        echo "ERROR: could not find cacerts under ${_home}" >&2; \
        exit 1; \
      fi; \
      if [ ! -e "$_target" ]; then \
        echo "WARNING: expected ${_target} to exist (from ca-certificates-java). Attempting to regenerate." >&2; \
        update-ca-certificates || true; \
      fi; \
      if [ -L "$_cacerts" ] && [ "$(readlink -f "$_cacerts")" = "$_target" ]; then \
        return 0; \
      fi; \
      echo "WARNING: ca-certificates-java did not configure ${_cacerts} -> ${_target} for ${_home}. Repairing." >&2; \
      rm -f "$_cacerts"; \
      ln -s "$_target" "$_cacerts"; \
    } && \
    \
    # Resolve suites and homes in a platform-agnostic way
    MS_ALT="$(update-java-alternatives -l | awk '$1 ~ /^msopenjdk-25/ {print $1; exit}')" && \
    MS_HOME="$(update-java-alternatives -l | awk '$1 ~ /^msopenjdk-25/ {print $3; exit}')" && \
    J8_ALT="$(update-java-alternatives -l | awk '$1 ~ /^java-1\.8\.0-openjdk/ {print $1; exit}')" && \
    J8_HOME="$(update-java-alternatives -l | awk '$1 ~ /^java-1\.8\.0-openjdk/ {print $3; exit}')" && \
    \
    if [ -z "$MS_ALT" ] || [ -z "$MS_HOME" ] || [ -z "$J8_ALT" ] || [ -z "$J8_HOME" ]; then \
      echo "ERROR: could not locate Java suites via update-java-alternatives -l" >&2; \
      update-java-alternatives -l >&2 || true; \
      exit 1; \
    fi && \
    \
    # Resolve symlinks so our stable links point at real directories
    MS_HOME="$(readlink -f "$MS_HOME")" && \
    J8_HOME="$(readlink -f "$J8_HOME")" && \
    \
    # Pin the entire Java alternatives suite to Java 25 (not just /usr/bin/java)
    update-java-alternatives --set "$MS_ALT" && \
    \
    # Stable cross-arch paths
    ln -sfn "$MS_HOME" /usr/lib/jvm/msopenjdk-25 && \
    ln -sfn "$J8_HOME" /usr/lib/jvm/jre8 && \
    \
    # Ensure both JVMs use the system-generated Java truststore
    link_java_cacerts "$MS_HOME" && \
    link_java_cacerts "$J8_HOME" && \
    \
    # Clean up the apt cache to reduce the final image size
    apt-get clean && \
    \
    # Remove unnecessary files to minimize image size
    rm -rf \
        /config/.cache \
        /var/lib/apt/lists/* \
        /var/tmp/* \
        /tmp/*

ENV JAVA_HOME=/usr/lib/jvm/msopenjdk-25
ENV JAVA8_HOME=/usr/lib/jvm/jre8
