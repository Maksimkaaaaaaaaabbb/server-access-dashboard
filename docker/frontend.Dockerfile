# Stage 1: Build the Vue.js App
# Use a Node.js Alpine image for a smaller build environment
FROM node:20-alpine AS builder

# Set the working directory in the build container
WORKDIR /app

# --- No VITE_* environment variables here ---
# Configuration is handled at runtime.

# Copy package.json and package-lock.json from the frontend folder
COPY frontend/package*.json ./

# Install all dependencies (including devDependencies for the build)
RUN npm install

# Copy the entire frontend source code from the frontend folder
COPY frontend/ ./

# Build the static files for production with Vite
# The result will be in the /app/dist folder
# Vite will NOT embed any environment variables, since no VITE_* are set.
RUN npm run build

# Stage 2: Serve the static files with 'serve' and runtime config
# Use the same slim Node.js Alpine image for running
FROM node:20-alpine

WORKDIR /app

# Install 'serve' globally. 'serve' is a simple static HTTP server.
RUN npm install -g serve

# Copy only the built artifacts from the 'builder' stage (from /app/dist)
# into the current directory of the final image.
COPY --from=builder /app/dist ./dist

# --- Create startup script (for URL and Key) ---
# This script will be executed when the container starts.
# It reads the environment variables API_URL and API_KEY
# and writes them to the file dist/config.json.
# Then it starts the 'serve' server.
RUN echo '#!/bin/sh' > /app/entrypoint.sh && \
    # Show the values of the environment variables in the log (without the key itself)
    echo 'echo "INFO: Configuring frontend with API_URL=${API_URL} and API_KEY=(set)"' >> /app/entrypoint.sh && \
    # Create the JSON file. Adds both apiUrl and apiKey.
    # Proper escaping for JSON strings within the shell command.
    echo "echo '{\"apiUrl\":\"'\${API_URL}'\",\"apiKey\":\"'\${API_KEY}'\"}' > /app/dist/config.json" >> /app/entrypoint.sh && \
    echo 'echo "INFO: Content of config.json:"' >> /app/entrypoint.sh && \
    # Output the content of the created file for verification
    echo 'cat /app/dist/config.json' >> /app/entrypoint.sh && \
    echo 'echo "INFO: Starting serve..."' >> /app/entrypoint.sh && \
    # Start the 'serve' server for the 'dist' folder on port 80
    # -s for single-page application handling
    echo 'exec serve -s dist -l 80' >> /app/entrypoint.sh && \
    # Make the script executable
    chmod +x /app/entrypoint.sh

# --- Environment variables for the runtime script ---
# These variables MUST be set via 'docker run' or docker-compose.
# The default values here are just placeholders.
ENV API_URL="http://please-set-api-url-in-compose:8000"
ENV API_KEY="please_set_api_key_in_compose"

# Expose port 80 (default HTTP port for 'serve', as specified in the script)
EXPOSE 80

# --- Use startup script as entrypoint ---
# This runs our script when the container starts.
ENTRYPOINT ["/app/entrypoint.sh"]
