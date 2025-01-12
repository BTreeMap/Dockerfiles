FROM python:slim

ENV TERM xterm
WORKDIR /app

# Copy the 'endpoint' folder from the existing image
COPY --from=christld/macless-haystack:latest /app/endpoint /app/endpoint

# Install necessary dependencies and Python packages in a single RUN command
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        nano \
        iproute2 \
        cron \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r endpoint/requirements.txt

# Run the application
CMD ["python", "endpoint/mh_endpoint.py"]
