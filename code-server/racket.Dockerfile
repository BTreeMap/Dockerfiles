# =============================================================================
# Standalone build of the Racket toolchain, published as :code-server-racket
#
# Extracted verbatim from the racket_builder stage of ./Dockerfile so it can be
# built as its own task, in parallel with the other toolchains, instead of
# serially inside the main image. The stage remains in ./Dockerfile for now;
# that copy is retired once this tag exists in the registry.
#
# Racket is a general-purpose, multi-paradigm programming language in the
# Lisp/Scheme family.
# =============================================================================
FROM ghcr.io/btreemap/dockerfiles:code-server-base AS racket_builder

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
