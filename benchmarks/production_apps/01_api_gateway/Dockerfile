# ==============================================================================
# Production App 1: High-Performance API Gateway & Microservices Engine
# Minimal Debian Bookworm base with NGINX reverse proxy & Python async backend
# ==============================================================================
FROM debian:bookworm-slim

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Install minimal required OS packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx-light \
    python3 \
    python3-venv \
    python3-pip \
    curl \
    procps \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# Set up dedicated Python virtual environment for isolation
WORKDIR /app
RUN python3 -m venv /opt/venv

# Install Python production dependencies
COPY requirements.txt /app/
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy NGINX configuration
COPY nginx.conf /etc/nginx/nginx.conf

# Copy backend application code and benchmark client
COPY app/ /app/app/
COPY benchmark_client.py /app/benchmark_client.py

# Copy and prepare entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /app/benchmark_client.py

# Forward NGINX logs to stdout/stderr container logs
RUN ln -sf /dev/stdout /var/log/nginx/access.log \
    && ln -sf /dev/stderr /var/log/nginx/error.log

# Expose HTTP proxy port 80 and internal backend port 8000
EXPOSE 80 8000

# Health check against native NGINX fast path
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1/healthz || exit 1

ENTRYPOINT ["/entrypoint.sh"]
