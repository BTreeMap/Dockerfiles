# Dockerfile for guacamole-server
# Reference: https://github.com/apache/guacamole-server/blob/1.5.5/Dockerfile

# Define the tag as an ARG for easy modification.
ARG GUACAMOLE_VERSION=1.5.5

# Use the arm64v8 variant of Alpine as the base image
ARG ALPINE_BASE_IMAGE=3.18
FROM arm64v8/alpine:${ALPINE_BASE_IMAGE} AS builder

# FreeRDP version (default to version 3)
ARG FREERDP_VERSION=3

# Install build dependencies
RUN apk add --no-cache                \
        autoconf                      \
        automake                      \
        build-base                    \
        cairo-dev                     \
        cjson-dev                     \
        cmake                         \
        cunit-dev                     \
        git                           \
        grep                          \
        krb5-dev                      \
        libjpeg-turbo-dev             \
        libpng-dev                    \
        libtool                       \
        libwebp-dev                   \
        make                          \
        openssl1.1-compat-dev         \
        pango-dev                     \
        pulseaudio-dev                \
        sdl2-dev                      \
        sdl2_ttf-dev                  \
        util-linux-dev                \
        webkit2gtk-dev                \
        unzip                         \
        bash                          \
        curl

# Set up a working directory
WORKDIR /tmp

# Clone the repository and checkout the specified tag
RUN git clone https://github.com/apache/guacamole-server.git && \
    cd guacamole-server && \
    git checkout ${GUACAMOLE_VERSION}

# Define the build directory
ARG BUILD_DIR=/tmp/guacamole-server

# Build guacamole-server and its core protocol library dependencies
RUN ${BUILD_DIR}/src/guacd-docker/bin/build-all.sh

# Determine location of the FREERDP library based on the version.
ARG FREERDP_LIB_PATH=${PREFIX_DIR}/lib/freerdp${FREERDP_VERSION}

# Record the packages of all runtime library dependencies
RUN ${BUILD_DIR}/src/guacd-docker/bin/list-dependencies.sh \
        ${PREFIX_DIR}/sbin/guacd               \
        ${PREFIX_DIR}/lib/libguac-client-*.so  \
        ${FREERDP_LIB_PATH}/*guac*.so   \
        > ${PREFIX_DIR}/DEPENDENCIES

# Use the same arm64v8 Alpine version as the base for the runtime image
FROM arm64v8/alpine:${ALPINE_BASE_IMAGE}

# Base directory for installed build artifacts.
ARG PREFIX_DIR=/opt/guacamole

# Runtime environment
ENV LC_ALL=C.UTF-8
ENV LD_LIBRARY_PATH=${PREFIX_DIR}/lib
ENV GUACD_LOG_LEVEL=info

# Copy build artifacts into this stage
COPY --from=builder ${PREFIX_DIR} ${PREFIX_DIR}

# Bring the runtime environment up to date and install runtime dependencies
RUN apk add --no-cache                \
        ca-certificates               \
        font-noto-cjk                 \
        ghostscript                   \
        netcat-openbsd                \
        shadow                        \
        terminus-font                 \
        ttf-dejavu                    \
        ttf-liberation                \
        util-linux-login && \
    xargs apk add --no-cache < ${PREFIX_DIR}/DEPENDENCIES

# Checks the operating status every 5 minutes with a timeout of 5 seconds
HEALTHCHECK --interval=5m --timeout=5s CMD nc -z 127.0.0.1 4822 || exit 1

# Create a new user guacd
ARG UID=1000
ARG GID=1000
RUN groupadd --gid $GID guacd
RUN useradd --system --create-home --shell /sbin/nologin --uid $UID --gid $GID guacd

# Run with user guacd
USER guacd

# Expose the default listener port
EXPOSE 4822

# Start guacd, listening on port 0.0.0.0:4822
CMD /opt/guacamole/sbin/guacd -b 0.0.0.0 -L $GUACD_LOG_LEVEL -f
