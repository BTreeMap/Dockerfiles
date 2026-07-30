# =============================================================================
# Standalone build of the eBPF tools, published as :code-server-ebpf-tools
#
# Extracted verbatim from the ebpf_tools_builder stage of ./Dockerfile so it can
# be built as its own task, in parallel with the other toolchains, instead of
# serially inside the main image. The stage remains in ./Dockerfile for now;
# that copy is retired once this tag exists in the registry.
#
# Currently includes bpftool, used for managing BPF programs and maps.
# =============================================================================
FROM ghcr.io/btreemap/dockerfiles:code-server-base AS ebpf_tools_builder

RUN set -ex && \
    # Determine target architecture
    ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64) ARCH=amd64 ;; \
        aarch64|arm64) ARCH=arm64 ;; \
        *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;; \
    esac && \
    echo "Fetching latest bpftool version" && \
    BPFTOOL_LATEST=$(curl -sSL https://api.github.com/repos/libbpf/bpftool/releases/latest | jq -r .tag_name) && \
    echo "Latest bpftool version: $BPFTOOL_LATEST" && \
    TARBALL="bpftool-${BPFTOOL_LATEST}-${ARCH}.tar.gz" && \
    URL="https://github.com/libbpf/bpftool/releases/download/${BPFTOOL_LATEST}/${TARBALL}" && \
    echo "Downloading $URL" && \
    curl -sSL -o /tmp/${TARBALL} ${URL} && \
    curl -sSL -o /tmp/${TARBALL}.sha256sum ${URL}.sha256sum && \
    (cd /tmp && sha256sum -c ${TARBALL}.sha256sum) && \
    mkdir -p $EBPF_TOOLS_HOME && \
    tar -C $EBPF_TOOLS_HOME -xzf /tmp/${TARBALL} && \
    # Make bpftool executable
    chmod +x $EBPF_TOOLS_HOME/bpftool && \
    rm -rf /tmp/*

# =============================================================================
# Artifact image - carries the tools and nothing else
#
# Scratch rather than the builder: this tag exists only to be read by a
# COPY --from, so republishing all of code-server-base underneath it twice a
# day would buy nothing.
#
# /opt/ebpf-tools is code-server-base's $EBPF_TOOLS_HOME, repeated literally
# because a scratch stage inherits no ENV. If the two ever diverge this COPY
# fails the build outright rather than publishing an empty image.
# =============================================================================
FROM scratch

COPY --from=ebpf_tools_builder /opt/ebpf-tools /opt/ebpf-tools
