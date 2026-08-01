# =============================================================================
# Main image - Using LinuxServer's code-server as the base image
# code-server provides VS Code in the browser with a full development environment
# =============================================================================
FROM ghcr.io/btreemap/dockerfiles:code-server-base

# Set environment variables for the final image
ENV CABAL_UPDATE_PACKAGES=true \
    PATCH_JSON=true \
    PATCH_JSON_ENABLE_SORT_KEYS=true \
    PATCH_JSON_USE_FORCE=true \
    PATH=$CABAL_DIR/bin:$HASKELL_HOME/.ghcup/bin:$FNM_DIR:$CARGO_HOME/bin:$GO_HOME/bin:$EBPF_TOOLS_HOME:$LLVM_HOME:$PATH \
    USER_INIT_DIR=/config/.entrypoint.d \
    USER_INIT_FILE=/config/.entrypoint \
    VENV_DIR=/opt/shared-venv

# Environment variables for Tailscale
ENV \
    TAILSCALE_ENABLED=false \
    TAILSCALE_STATE_DIR=/config/tailscale \
    TAILSCALE_DISABLE_LOGGING=false \
    TAILSCALE_AUTHKEY="" \
    TAILSCALE_HOSTNAME=code-server \
    TAILSCALE_USE_SSH=false \
    TAILSCALE_ACCEPT_DNS=true \
    TAILSCALE_REPLACE_DNS=false \
    TAILSCALE_AUTO_UPDATE=false \
    TAILSCALE_BE_EXIT_NODE=false \
    TAILSCALE_USE_WEBCLIENT=true \
    TAILSCALE_SERVE_MODE=https \
    TAILSCALE_SERVE_PORT=8443

# Environment variables for uv
ENV UV_INSTALL_DIR=/opt/uv \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_NO_MODIFY_PATH=1

## Copy application files and s6-overlay config to container root
COPY root/ /

# -----------------------------------------------------------------------------
# Toolchains, copied from the images ./proot.Dockerfile, ./haskell.Dockerfile,
# ./go.Dockerfile, and ./ebpf-tools.Dockerfile publish.
#
# Racket is deliberately absent -- it is the one toolchain here nothing was
# actually using, and it is the largest. It now layers back on top of this image
# in ./full.Dockerfile, so anyone who wants it pulls :code-server-full instead.
#
# These were builder stages in this file until every one of them had to finish
# before this image could start. As separate tasks the mesh spreads them across
# runners and they build concurrently, which is the whole point.
#
# Each source is a scratch image holding only the artifact, so these pulls are
# the toolchain and nothing else. The tags float, so a build picks up whatever
# the last completed run published -- at most 12 hours old on the current
# schedule, and the deliberate cost of decoupling them.
#
# The --chown stays: the artifact images carry root-owned files, so ownership is
# established here, exactly as it was when these were local stages.
# -----------------------------------------------------------------------------

# proot lets us create a virtualized filesystem environment without full root
# privileges. Renamed on the way in, as it was from the builder stage.
COPY --from=ghcr.io/btreemap/dockerfiles:code-server-proot \
    /proot/dist/proot /usr/local/bin/proot

# Haskell (GHC, Cabal, HLS), for development and execution of Haskell programs
COPY --from=ghcr.io/btreemap/dockerfiles:code-server-haskell \
    --chown=${PUID}:${PGID} $HASKELL_HOME $HASKELL_HOME

# Go, for development and execution of Go programs
COPY --from=ghcr.io/btreemap/dockerfiles:code-server-go \
    --chown=${PUID}:${PGID} $GO_HOME $GO_HOME

# bpftool, for BPF-related operations
COPY --from=ghcr.io/btreemap/dockerfiles:code-server-ebpf-tools \
    --chown=${PUID}:${PGID} $EBPF_TOOLS_HOME $EBPF_TOOLS_HOME

RUN set -ex && \
    \
    # Install fnm binary
    curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell --install-dir $FNM_DIR && \
    \
    # Hook fnm into the current build step
    eval "$($FNM_DIR/fnm env --shell bash)" && \
    \
    # Download Node LTS and activate it in the current shell simultaneously
    fnm use --install-if-missing lts/latest && \
    \
    # Upgrade npm and install global AI CLIs (npm is now instantly available)
    npm install -g npm@latest --no-audit --no-fund && \
    npm install -g @openai/codex@latest --no-audit --no-fund && \
    npm install -g @anthropic-ai/claude-code@latest --no-audit --no-fund --allow-scripts=@anthropic-ai/claude-code && \
    \
    # Clear npm cache
    npm cache clean --force && \
    \
    # Install Antigravity CLI globally
    curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin && \
    \
    # Fix ownership for the fnm directory
    chown -R ${PUID}:${PGID} $FNM_DIR && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    find /config /tmp /var/tmp /var/lib/apt/lists -mindepth 1 -delete

RUN set -ex && \
    \
    # Install Rust using rustup
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    chown -R ${PUID}:${PGID} $RUSTUP_HOME && \
    chown -R ${PUID}:${PGID} $CARGO_HOME && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    find /config /tmp /var/tmp /var/lib/apt/lists -mindepth 1 -delete

RUN set -ex && \
    \
    # Install Microsoft's OpenJDK 21
    wget https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/packages-microsoft-prod.deb -O packages-microsoft-prod.deb && \
    dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends msopenjdk-21 && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    find /config /tmp /var/tmp /var/lib/apt/lists -mindepth 1 -delete

RUN set -ex && \
    \
    # Install the GitHub CLI from its own apt repository
    mkdir -p -m 755 /etc/apt/keyrings && \
    curl -fsSLo /etc/apt/keyrings/githubcli-archive-keyring.gpg https://cli.github.com/packages/githubcli-archive-keyring.gpg && \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    find /config /tmp /var/tmp /var/lib/apt/lists -mindepth 1 -delete

RUN set -ex && \
    \
    # Install Tailscale
    curl -fsSL https://tailscale.com/install.sh | sh && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    find /config /tmp /var/tmp /var/lib/apt/lists -mindepth 1 -delete

RUN set -ex && \
    \
    # Install the specified LLVM/Clang version
    curl -fsSL https://apt.llvm.org/llvm.sh | bash -s -- ${LLVM_VERSION} all && \
    \
    # Symlink the most popular clang/llvm commands to their -${LLVM_VERSION} variants
    mkdir -p $LLVM_HOME && \
    for tool in \
        clang clang++ clang-format clang-tidy clangd \
        lldb lld llc \
        llvm-ar llvm-as llvm-link llvm-nm llvm-objdump \
        llvm-profdata llvm-cov; \
    do \
        ln -sf /usr/bin/${tool}-${LLVM_VERSION} ${LLVM_HOME}/${tool} && command -v ${tool}; \
    done && \
    chown -R ${PUID}:${PGID} $LLVM_HOME && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    find /config /tmp /var/tmp /var/lib/apt/lists -mindepth 1 -delete

ARG UV_NO_CACHE=1

RUN set -ex && \
    \
    # Install some Python packages using pip
    # Install uv and uvx using the official installer script (latest)
    mkdir -p ${UV_INSTALL_DIR} && \
    curl -LsSf https://astral.sh/uv/install.sh | sh && \
    ln -sf ${UV_INSTALL_DIR}/uv /usr/local/bin/uv && \
    ln -sf ${UV_INSTALL_DIR}/uvx /usr/local/bin/uvx && \
    mkdir -p ${UV_TOOL_DIR} && \
    uv --version && \
    uvx --version && \
    \
    # Create Python virtual environment as root then change ownership
    uv venv ${VENV_DIR} && \
    chown -R ${PUID}:${PGID} ${VENV_DIR} && \
    # Install packages as the specified user
    s6-setuidgid ${PUID}:${PGID} \
        uv pip install --python ${VENV_DIR}/bin/python \
        aiodns \
        aiohttp \
        aioitertools \
        aiomultiprocess \
        aiosqlite \
        anthropic \
        beautifulsoup4 \
        click \
        cohere \
        cryptography \
        discord.py \
        Django \
        Flask \
        google-cloud-firestore \
        google-cloud-storage \
        google-genai \
        httpx[brotli,http2,socks,zstd] \
        json5 \
        jupyter \
        langchain \
        langchain-community \
        langgraph \
        loguru \
        matplotlib \
        numpy \
        openai \
        orjson \
        pandas \
        pillow \
        psycopg[binary,pool] \
        pydantic \
        pymongo \
        pypdf \
        pytest \
        python-dateutil \
        python-dotenv \
        python-telegram-bot \
        PyYAML \
        reedsolo \
        requests[socks] \
        scapy \
        scikit-learn \
        scipy \
        seaborn \
        skl2onnx \
        sympy \
        tqdm \
        twilio \
        ujson \
        websockets \
    && \
    # Install global CLI tools using uv tool
    uv tool install httpie@latest && \
    uv tool install meson@latest && \
    uv tool install ruff@latest && \
    uv tool install wheel@latest && \
    \
    # Remove temporary and unnecessary files
    apt-get clean && \
    find /config /tmp /var/tmp /var/lib/apt/lists -mindepth 1 -delete

# Expose the necessary port
EXPOSE 8443
