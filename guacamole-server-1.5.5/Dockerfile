# Dockerfile for guacamole-server
# Reference: https://github.com/apache/guacamole-server/blob/1.5.5/Dockerfile

# Use Alpine as the base image
ARG ALPINE_BASE_IMAGE=3.18

# Use the build platform argument
ARG TARGETPLATFORM

FROM alpine:${ALPINE_BASE_IMAGE} AS builder

ENV GUACAMOLE_SOURCE_DIR="/tmp/guacamole-source"
ENV GUACAMOLE_VERSION=1.5.5
ENV GUACAMOLE_REPOSITORY=https://github.com/BTreeMap/guacamole-server-2025-02-26-archive.git

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
        webkit2gtk-dev

# Clone the repository and checkout the specified tag
WORKDIR $GUACAMOLE_SOURCE_DIR
RUN git clone --depth 1 --branch $GUACAMOLE_VERSION $GUACAMOLE_REPOSITORY $GUACAMOLE_SOURCE_DIR/guacamole-server
WORKDIR $GUACAMOLE_SOURCE_DIR/guacamole-server

# Copy source to container for sake of build
ARG BUILD_DIR=/tmp/guacamole-server
RUN mkdir -p ${BUILD_DIR} && cp -a . ${BUILD_DIR}

#
# Base directory for installed build artifacts.
#
# NOTE: Due to limitations of the Docker image build process, this value is
# duplicated in an ARG in the second stage of the build.
#
ARG PREFIX_DIR=/opt/guacamole

#
# Automatically select the latest versions of each core protocol support
# library (these can be overridden at build time if a specific version is
# needed)
#
ARG WITH_FREERDP='2(\.\d+)+'
ARG WITH_LIBSSH2='libssh2-\d+(\.\d+)+'
ARG WITH_LIBTELNET='\d+(\.\d+)+'
ARG WITH_LIBVNCCLIENT='LibVNCServer-\d+(\.\d+)+'
ARG WITH_LIBWEBSOCKETS='v\d+(\.\d+)+'

#
# Default build options for each core protocol support library, as well as
# guacamole-server itself (these can be overridden at build time if different
# options are needed)
#

# Allow users to override FREERDP_OPTS via build arguments
ARG FREERDP_OPTS=""

# Define the common FREERDP_OPTS without the -DWITH_SSE2 option
ENV FREERDP_OPTS_COMMON="\
    -DBUILTIN_CHANNELS=OFF \
    -DCHANNEL_URBDRC=OFF \
    -DWITH_ALSA=OFF \
    -DWITH_CAIRO=ON \
    -DWITH_CHANNELS=ON \
    -DWITH_CLIENT=ON \
    -DWITH_CUPS=OFF \
    -DWITH_DIRECTFB=OFF \
    -DWITH_FFMPEG=OFF \
    -DWITH_GSM=OFF \
    -DWITH_GSSAPI=OFF \
    -DWITH_IPP=OFF \
    -DWITH_JPEG=ON \
    -DWITH_LIBSYSTEMD=OFF \
    -DWITH_MANPAGES=OFF \
    -DWITH_OPENH264=OFF \
    -DWITH_OPENSSL=ON \
    -DWITH_OSS=OFF \
    -DWITH_PCSC=OFF \
    -DWITH_PULSE=OFF \
    -DWITH_SERVER=OFF \
    -DWITH_SERVER_INTERFACE=OFF \
    -DWITH_SHADOW_MAC=OFF \
    -DWITH_SHADOW_X11=OFF \
    -DWITH_WAYLAND=OFF \
    -DWITH_X11=OFF \
    -DWITH_X264=OFF \
    -DWITH_XCURSOR=ON \
    -DWITH_XEXT=ON \
    -DWITH_XI=OFF \
    -DWITH_XINERAMA=OFF \
    -DWITH_XKBFILE=ON \
    -DWITH_XRENDER=OFF \
    -DWITH_XTEST=OFF \
    -DWITH_XV=OFF \
    -DWITH_ZLIB=ON"

ARG GUACAMOLE_SERVER_OPTS="\
    --disable-guaclog"

ARG LIBSSH2_OPTS="\
    -DBUILD_EXAMPLES=OFF \
    -DBUILD_SHARED_LIBS=ON"

ARG LIBTELNET_OPTS="\
    --disable-static \
    --disable-util"

ARG LIBVNCCLIENT_OPTS=""

ARG LIBWEBSOCKETS_OPTS="\
    -DDISABLE_WERROR=ON \
    -DLWS_WITHOUT_SERVER=ON \
    -DLWS_WITHOUT_TESTAPPS=ON \
    -DLWS_WITHOUT_TEST_CLIENT=ON \
    -DLWS_WITHOUT_TEST_PING=ON \
    -DLWS_WITHOUT_TEST_SERVER=ON \
    -DLWS_WITHOUT_TEST_SERVER_EXTPOLL=ON \
    -DLWS_WITH_STATIC=OFF"

# Export TARGETPLATFORM as an environment variable
ENV TARGETPLATFORM=${TARGETPLATFORM}

# Build guacamole-server and its core protocol library dependencies
RUN set -ex; \
    \
    # Check if FREERDP_OPTS is already set
    if [ -z "${FREERDP_OPTS}" ]; then \
        # Determine the -DWITH_SSE2 option based on TARGETPLATFORM
        if [ "${TARGETPLATFORM}" = "linux/amd64" ]; then \
            DWITH_SSE2="-DWITH_SSE2=ON"; \
            echo "Enabling SSE2 support for ${TARGETPLATFORM}"; \
        else \
            DWITH_SSE2="-DWITH_SSE2=OFF"; \
            echo "Disabling SSE2 support for ${TARGETPLATFORM}"; \
        fi; \
        \
        # Combine FREERDP_OPTS_COMMON with DWITH_SSE2 to form FREERDP_OPTS
        FREERDP_OPTS="${FREERDP_OPTS_COMMON} ${DWITH_SSE2}"; \
    else \
        echo "Using custom FREERDP_OPTS: ${FREERDP_OPTS}"; \
    fi; \
    \
    echo "Final FREERDP_OPTS: ${FREERDP_OPTS}"; \
    \
    export FREERDP_OPTS; \
    \
    # Call build-all.sh
    ${BUILD_DIR}/src/guacd-docker/bin/build-all.sh

# Record the packages of all runtime library dependencies
RUN ${BUILD_DIR}/src/guacd-docker/bin/list-dependencies.sh \
        ${PREFIX_DIR}/sbin/guacd               \
        ${PREFIX_DIR}/lib/libguac-client-*.so  \
        ${PREFIX_DIR}/lib/freerdp2/*guac*.so   \
        > ${PREFIX_DIR}/DEPENDENCIES

# Use same Alpine version as the base for the runtime image
FROM alpine:${ALPINE_BASE_IMAGE}

#
# Base directory for installed build artifacts. See also the
# CMD directive at the end of this build stage.
#
# NOTE: Due to limitations of the Docker image build process, this value is
# duplicated in an ARG in the first stage of the build.
#
ARG PREFIX_DIR=/opt/guacamole

# Runtime environment
ENV LC_ALL=C.UTF-8
ENV LD_LIBRARY_PATH=${PREFIX_DIR}/lib
ENV GUACD_LOG_LEVEL=info

# Copy build artifacts into this stage
COPY --from=builder ${PREFIX_DIR} ${PREFIX_DIR}

# Bring runtime environment up to date and install runtime dependencies
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
#
# Note the path here MUST correspond to the value specified in the 
# PREFIX_DIR build argument.
#
CMD /opt/guacamole/sbin/guacd -b 0.0.0.0 -L $GUACD_LOG_LEVEL -f
