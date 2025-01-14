FROM python:slim

ENV FINDER_UPDATE_INTERVAL=300
ENV LOCATION_SERVER_PORT=27184

ENV TERM=xterm
ENV PIP_ROOT_USER_ACTION=ignore

ARG DEBIAN_FRONTEND=noninteractive

# Copy the 'endpoint' folder from the existing image
COPY --from=christld/macless-haystack:latest /app/endpoint /app/endpoint

# Copy all necessary files to the correct path
# Assuming that 'root/' contains 'check_config.py', 'entrypoint.sh', and the 'endpoint' directory
COPY root/ /

WORKDIR /app

# Install necessary dependencies and Python packages in as few RUN commands as possible
RUN apt-get update && \
    apt-get -y upgrade && \
    apt-get install -y --no-install-recommends \
        curl \
        nano \
        iproute2 \
        cron && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/endpoint/requirements.txt && \
    pip install --no-cache-dir requests cryptography pandas folium && \
    chmod +x /app/entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
