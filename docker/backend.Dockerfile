# Stage 1: Build Environment
FROM python:3.11-slim AS builder

WORKDIR /app

# Create venv
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies
COPY backend/app/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime Environment
FROM python:3.11-slim

# Install curl and tar for entrypoint script
RUN apt-get update && apt-get install -y curl tar && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy venv
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy app code
COPY backend/app /app


# ==Entrypoint Script==
RUN echo '#!/bin/sh' > /entrypoint.sh && \
    echo 'set -e' >> /entrypoint.sh && \
    echo 'DB_PATH="/app/database/GeoLite2-Country.mmdb"' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Check if the license key environment variable is provided' >> /entrypoint.sh && \
    echo 'if [ -z "${MAXMIND_LICENSE_KEY}" ]; then' >> /entrypoint.sh && \
    echo '  echo "Error: MAXMIND_LICENSE_KEY environment variable is not set."' >> /entrypoint.sh && \
    echo '  echo "Please add your MaxMind license key to your docker-compose.yml file."' >> /entrypoint.sh && \
    echo '  exit 1' >> /entrypoint.sh && \
    echo 'fi' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Check if the database file does NOT exist' >> /entrypoint.sh && \
    echo 'if [ ! -f "$DB_PATH" ]; then' >> /entrypoint.sh && \
    echo '  echo "GeoLite2 database not found. Starting download..."' >> /entrypoint.sh && \
    echo '  curl -L "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-Country&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz" -o /tmp/geoip.tar.gz && \' >> /entrypoint.sh && \
    echo '  tar -xvzf /tmp/geoip.tar.gz -C /tmp --strip-components=1 && \' >> /entrypoint.sh && \
    echo '  mv /tmp/GeoLite2-Country.mmdb "$DB_PATH"' >> /entrypoint.sh && \
    echo '  echo "Download and installation of GeoLite2 database complete."' >> /entrypoint.sh && \
    echo 'else' >> /entrypoint.sh && \
    echo '  echo "GeoLite2 database already exists. Skipping download."' >> /entrypoint.sh && \
    echo 'fi' >> /entrypoint.sh && \
    echo '' >> /entrypoint.sh && \
    echo '# Execute the main command passed to the script (e.g., uvicorn)' >> /entrypoint.sh && \
    echo 'exec "$@"' >> /entrypoint.sh

RUN chmod +x /entrypoint.sh



# Create database directory (which will be mounted as a volume)
RUN mkdir -p /app/database && \
    chmod 777 /app/database

# Environment variables for internal paths
ENV DATABASE_URL=sqlite:////app/database/logs.db
ENV GEOIP_DATABASE_PATH=/app/database/GeoLite2-Country.mmdb
ENV LOG_DIRECTORY=/nginx_logs
ENV NGINX_LOG_FORMAT='[$time_local] - - $status - $request_method $scheme $host $request_uri [Client $remote_addr] [Length $body_bytes_sent] [Gzip $gzip_info] [Sent-to $upstream_info] "$http_referer" "$http_user_agent"'
ENV API_KEY="default_insecure_key_please_overwrite"
ENV LOG_COLLECTION_INTERVAL_MINUTES="10"

# Expose port
EXPOSE 8000

# Set the new script as the ENTRYPOINT
ENTRYPOINT ["/entrypoint.sh"]

WORKDIR /
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
