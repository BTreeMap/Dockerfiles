# Dockerfile for guacamole-client
# Reference: https://github.com/apache/guacamole-client/blob/1.5.5/Dockerfile

# Define the tag as an ARG for easy modification.
ARG GUACAMOLE_VERSION=1.5.5

# Use args for Tomcat image label to allow image builder to choose alternatives
# such as `--build-arg TOMCAT_JRE=jre8-alpine`
#
ARG TOMCAT_VERSION=8.5
ARG TOMCAT_JRE=jdk8

# Use official maven image for the build
FROM maven:3-eclipse-temurin-8-focal AS builder

ENV GUACAMOLE_SOURCE_DIR="/tmp/guacamole-client-source"

RUN apt-get update              \
    && apt-get upgrade -y       \
    && apt-get install -y git

# Clone the repository and checkout the specified tag
WORKDIR $GUACAMOLE_SOURCE_DIR
RUN git clone --depth 1 --branch ${GUACAMOLE_VERSION} https://github.com/apache/guacamole-client.git $GUACAMOLE_SOURCE_DIR

# Use Mozilla's Firefox PPA (newer Ubuntu lacks a "firefox-esr" package and
# provides only a transitional "firefox" package that actually requires Snap
# and thus can't be used within Docker)
RUN    apt-get update                                \
    && apt-get upgrade -y                            \
    && apt-get install -y software-properties-common \
    && add-apt-repository -y ppa:mozillateam/ppa

# Explicitly prefer packages from the Firefox PPA
RUN cp guacamole-docker/mozilla-firefox.pref /etc/apt/preferences.d/

# Install firefox browser for sake of JavaScript unit tests
RUN apt-get update && apt-get install -y firefox

# Arbitrary arguments that can be passed to the maven build. By default, an
# argument will be provided to explicitly unskip any skipped tests. To, for
# example, allow the building of the RADIUS auth extension, pass a build profile
# as well: `--build-arg MAVEN_ARGUMENTS="-P lgpl-extensions -DskipTests=false"`.
ARG MAVEN_ARGUMENTS="-DskipTests=false"

# Versions of JDBC drivers to bundle within image
ARG MSSQL_JDBC_VERSION=9.4.1
ARG MYSQL_JDBC_VERSION=8.3.0
ARG PGSQL_JDBC_VERSION=42.7.2

# Build environment variables
ENV \
    BUILD_DIR=/tmp/guacamole-docker-BUILD

# Add configuration scripts
RUN cp guacamole-docker/bin/ /opt/guacamole/bin/

# Copy source to container for sake of build
RUN cp . "$BUILD_DIR"

# Run the build itself
RUN /opt/guacamole/bin/build-guacamole.sh "$BUILD_DIR" /opt/guacamole

# For the runtime image, we start with the official Tomcat distribution
FROM arm64v8/tomcat:${TOMCAT_VERSION}-${TOMCAT_JRE}

# Install XMLStarlet for server.xml alterations and unzip for LOGBACK_LEVEL case
RUN apt-get update -qq \
    && apt-get install -y xmlstarlet unzip\
    && rm -rf /var/lib/apt/lists/* 

# This is where the build artifacts go in the runtime image
WORKDIR /opt/guacamole

# Copy artifacts from builder image into this image
COPY --from=builder /opt/guacamole/ .

# Create a new user guacamole
ARG UID=1001
ARG GID=1001
RUN groupadd --gid $GID guacamole
RUN useradd --system --create-home --shell /usr/sbin/nologin --uid $UID --gid $GID guacamole

# Run with user guacamole
USER guacamole

# Start Guacamole under Tomcat, listening on 0.0.0.0:8080
EXPOSE 8080
CMD ["/opt/guacamole/bin/start.sh" ]
