FROM python:slim

ENV TERM xterm
WORKDIR /app

# Copy all necessary files to the correct path
# Assuming that 'root/' contains 'check_config.py', 'entrypoint.sh', and the 'endpoint' directory
COPY root/ /

# Install necessary dependencies and Python packages in as few RUN commands as possible
RUN apt-get update && \
    apt-get -y upgrade && \
    apt-get install -y --no-install-recommends \
        curl \
        nano \
        iproute2 \
        cron && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/endpoint/requirements.txt && \
    pip install --no-cache-dir requests cryptography pandas folium && \
    chmod +x /app/entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
