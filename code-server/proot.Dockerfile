# =============================================================================
# Standalone build of the proot binary, published as :code-server-proot
#
# Extracted verbatim from the proot_builder stage of ./Dockerfile so it can be
# built as its own task, in parallel with the other toolchains, instead of
# serially inside the main image. The stage remains in ./Dockerfile for now;
# that copy is retired once this tag exists in the registry.
#
# proot allows for containerized chroot-like functionality without root
# privileges.
# =============================================================================
FROM gcc:12.4.0-bookworm AS proot_builder

# Specify the proot version to ensure reproducible builds
ARG PROOT_VERSION=v5.4.0
ARG PROOT_REPOSITORY=https://github.com/BTreeMap/proot-2025-02-26-archive.git

# Install required build dependencies:
# - git: For source code retrieval
# - clang-tools: For static analysis during build
# - libarchive-dev: For archive manipulation support
# - libtalloc-dev: For memory allocation pooling
# - Other tools for building, testing and documentation
RUN set -eux && \
    apt-get update -y && \
    apt-get upgrade -y && \
    apt-get install -y \
        clang-tools-14 \
        curl \
        docutils-common \
        gdb \
        git \
        lcov \
        libarchive-dev \
        libtalloc-dev \
        strace \
        swig \
        uthash-dev \
        xsltproc

# Clone the specific version of proot from GitHub
# Using --depth 1 to minimize download size (shallow clone)
RUN git clone --depth 1 --branch $PROOT_VERSION $PROOT_REPOSITORY /proot

# Set the working directory for build operations
WORKDIR /proot

# Compile a static version of proot for maximum portability
# Static linking ensures the binary can run without external dependencies
# The resulting binary will be copied to the final image
RUN LDFLAGS="${LDFLAGS} -static" make -C src proot GIT=false && \
    mkdir -p dist && \
    cp src/proot dist/

# =============================================================================
# Artifact image - carries the binary and nothing else
#
# Scratch rather than the gcc builder: this tag exists only to be read by a
# COPY --from, so publishing the ~1.5 GB toolchain twice a day would buy
# nothing. The path matches the one ./Dockerfile already copies from, which is
# what lets the consumer switch by changing --from alone.
# =============================================================================
FROM scratch

COPY --from=proot_builder /proot/dist/proot /proot/dist/proot
