# System Gateway - Sandboxed execution environment for Mira
# Provides filesystem and command execution capabilities in an isolated container

FROM python:3.11-slim

# Build arguments
ARG USER_UID=1000
ARG USER_GID=1000

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # For watchdog/inotify filesystem monitoring
    inotify-tools \
    # Common development tools that might be needed in workspace
    git \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd --gid ${USER_GID} gateway \
    && useradd --uid ${USER_UID} --gid ${USER_GID} -m -s /bin/bash gateway

# Create application directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=gateway:gateway . .

# Create workspace mount point and temp directory
RUN mkdir -p /workspace /tmp/gateway \
    && chown -R gateway:gateway /workspace /tmp/gateway

# Switch to non-root user
USER gateway

# Expose gateway port
EXPOSE 9500

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:9500/health || exit 1

# Run the gateway service
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9500"]

