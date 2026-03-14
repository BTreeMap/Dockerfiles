# Use the rdesktop image based on Ubuntu with KDE (multi-arch: amd64 + arm64)
FROM lscr.io/linuxserver/rdesktop:ubuntu-kde

# Set the apt frontend to non-interactive mode for automated installs
ARG DEBIAN_FRONTEND=noninteractive

# Persisted tool installs (avoid /config, which is a mount in LinuxServer images)
ENV \
  FNM_DIR=/opt/fnm \
  UV_INSTALL_DIR=/opt/uv \
  GO_HOME=/opt/go

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
        fonts-noto-cjk \
        fonts-noto-cjk-extra \
        fonts-noto-color-emoji \
        gawk \
        git \
        gnupg \
        iproute2 \
        iptables \
        iputils-ping \
        jq \
        lsb-release \
        make \
        mtr \
        nano \
        okular \
        python-is-python3 \
        ripgrep \
        screen \
        tmux \
        unzip \
        vim \
        wget \
        xz-utils \
        yq \
    && \
    update-alternatives --set awk /usr/bin/gawk && \
    \
    # Install the latest stable Go toolchain into /opt/go
    # Follow code-server pattern: fetch JSON, pick latest stable, verify checksum, extract, clean
    ARCH=$(uname -m) && \
    case "$ARCH" in \
      x86_64) ARCH=amd64 ;; \
      aarch64|arm64) ARCH=arm64 ;; \
      *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;; \
    esac && \
    GO_JSON="$(curl -sSfL 'https://go.dev/dl/?mode=json')" && \
    GO_VERSION="$(echo "$GO_JSON" | jq -r '[.[] | select(.stable)][0].version')" && \
    TARBALL="${GO_VERSION}.linux-${ARCH}.tar.gz" && \
    GO_SHA256="$(echo "$GO_JSON" | jq -r --arg GO_VERSION "$GO_VERSION" --arg TARBALL "$TARBALL" '.[] | select(.version == $GO_VERSION) | .files[] | select(.filename == $TARBALL) | .sha256')" && \
    echo "Installing ${GO_VERSION} for ${ARCH}" && \
    curl -sSfL -o /tmp/go.tgz "https://go.dev/dl/${TARBALL}" && \
    echo "${GO_SHA256}  /tmp/go.tgz" | sha256sum -c - && \
    tar -C /opt -xzf /tmp/go.tgz && \
    ln -sf "$GO_HOME/bin/go" /usr/local/bin/go && \
    ln -sf "$GO_HOME/bin/gofmt" /usr/local/bin/gofmt && \
    rm -rf /tmp/go.tgz && \
    $GO_HOME/bin/go version && \
    \
    # Install uv into a persisted location and symlink into /usr/local/bin
    mkdir -p "${UV_INSTALL_DIR}" && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -sf "${UV_INSTALL_DIR}/uv" /usr/local/bin/uv && \
    ln -sf "${UV_INSTALL_DIR}/uvx" /usr/local/bin/uvx && \
    \
    # Install fnm into a persisted location (avoid /config)
    mkdir -p "${FNM_DIR}" && \
    curl -fsSL https://fnm.vercel.app/install | bash -s -- \
      --install-dir "${FNM_DIR}" \
      --skip-shell && \
    ln -sf "${FNM_DIR}/fnm" /usr/local/bin/fnm && \
    \
    # Configure this build shell to see the Node selected by fnm
    eval "$(fnm env --use-on-cd --shell bash)" && \
    \
    # Install and use latest Node LTS, then upgrade npm and install Gemini CLI
    fnm use lts/latest --install-if-missing && \
    npm install -g npm@latest --no-audit --no-fund && \
    npm install -g @google/gemini-cli@latest --no-audit --no-fund && \
    npm install -g @openai/codex@latest --no-audit --no-fund && \
    node -v && \
    npm -v && \
    npm cache clean --force && \
    rm -rf /root/.npm /root/.cache && \
    \
    # System-wide shell integration for fnm (/etc/profile.d is sufficient for our use cases)
    printf '%s\n' 'eval "$(fnm env --use-on-cd --shell bash)"' \
      > /etc/profile.d/fnm.sh && \
    chmod 0644 /etc/profile.d/fnm.sh && \
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
    # Add Google Antigravity apt repository
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg | \
      gpg --dearmor --yes -o /etc/apt/keyrings/antigravity-repo-key.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/antigravity-repo-key.gpg] https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/ antigravity-debian main" \
      > /etc/apt/sources.list.d/antigravity.list && \
    \
    # Update package lists again after adding repositories
    apt-get update && \
    \
    # Ensure the man1 directory exists for storing manual pages
    mkdir -p /usr/share/man/man1 && \
    \
    # Install Microsoft OpenJDK 25, OpenJDK 8, and Google Antigravity
    apt-get install -y msopenjdk-25 openjdk-8-jre antigravity && \
    \
    # Patch the Antigravity desktop entry to launch with a clean D-Bus session
    # and stable software-rendering defaults inside the container.
    desktop_file="/usr/share/applications/antigravity.desktop" && \
    main_exec_old='Exec=/usr/share/antigravity/antigravity %F' && \
    main_exec_new='Exec=env -u DBUS_SESSION_BUS_ADDRESS dbus-run-session -- /usr/share/antigravity/antigravity --password-store=basic --disable-gpu --disable-gpu-compositing %F' && \
    new_window_exec_old='Exec=/usr/share/antigravity/antigravity --new-window %F' && \
    new_window_exec_new='Exec=env -u DBUS_SESSION_BUS_ADDRESS dbus-run-session -- /usr/share/antigravity/antigravity --password-store=basic --disable-gpu --disable-gpu-compositing --new-window %F' && \
    if [ -f "$desktop_file" ]; then \
      if grep -Fqx "$main_exec_old" "$desktop_file"; then \
        sed -i "s|^${main_exec_old}$|${main_exec_new}|" "$desktop_file"; \
      else \
        echo "WARNING: expected main Exec line not found in ${desktop_file}; leaving it unchanged" >&2; \
      fi; \
      if grep -Fqx "$new_window_exec_old" "$desktop_file"; then \
        sed -i "s|^${new_window_exec_old}$|${new_window_exec_new}|" "$desktop_file"; \
      else \
        echo "WARNING: expected new-window Exec line not found in ${desktop_file}; leaving it unchanged" >&2; \
      fi; \
    else \
      echo "WARNING: ${desktop_file} not found; skipping Antigravity desktop entry patch" >&2; \
    fi && \
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
