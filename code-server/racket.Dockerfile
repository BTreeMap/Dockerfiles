# =============================================================================
# Standalone build of the Racket toolchain, published as :code-server-racket
#
# Extracted verbatim from the racket_builder stage of ./Dockerfile so it can be
# built as its own task, in parallel with the other toolchains, instead of
# serially inside the main image.
#
# ./full.Dockerfile is now the only image that reads this tag -- ./Dockerfile
# dropped its copy when Racket was trimmed out of the default image. The build
# stays: it is what makes :code-server-full a thin layer rather than a fork.
#
# Racket is a general-purpose, multi-paradigm programming language in the
# Lisp/Scheme family.
# =============================================================================
# The batch this build is pinned to, empty by default so this file still builds
# standalone against the floating tag. Only the batch is negotiable -- the image
# is literal above, so a build argument can sharpen this reference but never
# redirect it. See ci/domain.selector.
# One argument per reference from this repository, each defaulting to the
# reference itself. Without the build system this file resolves exactly these
# images; with it, every argument is replaced by the same image in the registry
# being published to, pinned to a batch. The argument names are free: the build
# system reads each one off the declaration it appears in.
ARG CODE_SERVER_BASE=ghcr.io/btreemap/dockerfiles:code-server-base
FROM ${CODE_SERVER_BASE} AS racket_builder

# Set environment variables for Racket installation
ENV BUILD_DIR=/build

# Specify the Racket version and download URL
ARG RACKET_VERSION=8.16
ARG RACKET_PACKAGE=racket-8.16-src-builtpkgs.tgz
ARG RACKET_URL=https://download.racket-lang.org/releases/$RACKET_VERSION/installers/$RACKET_PACKAGE
ARG RACKET_CHECKSUM=sha256:44d7c1ab34b52588f90dc22b15d96110e104d0c88ed1869f85b6f03c99843078

# Download the Racket source package
ADD --checksum=$RACKET_CHECKSUM $RACKET_URL $BUILD_DIR/$RACKET_PACKAGE

# Set the working directory for build operations
WORKDIR $BUILD_DIR

# Extract, configure, build, and install Racket
RUN tar xfz $RACKET_PACKAGE && \
    cd racket-$RACKET_VERSION/src && \
    ./configure --prefix=$RACKET_HOME && \
    make -j$(nproc) && \
    make install

# =============================================================================
# Artifact image - carries the installation and nothing else
#
# Scratch rather than the builder: this tag exists only to be read by a
# COPY --from, so republishing all of code-server-base underneath it twice a
# day would buy nothing.
#
# /opt/racket is code-server-base's $RACKET_HOME, repeated literally because a
# scratch stage inherits no ENV. If the two ever diverge this COPY fails the
# build outright rather than publishing an empty image.
# =============================================================================
FROM scratch

COPY --from=racket_builder /opt/racket /opt/racket
