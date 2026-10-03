# ==============================================================================
# Production App 2: In-Memory Redis Cache Engine
# Production-tuned Redis 7 with Persistence, Memory Eviction, and Multi-threaded I/O
# ==============================================================================
FROM redis:7-bookworm

# Install performance diagnostic tools and process privilege helpers
RUN apt-get update && apt-get install -y --no-install-recommends \
    gosu \
    procps \
    sysstat \
    curl \
    jq \
    bc \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set up directories
RUN mkdir -p /usr/local/etc/redis /data && chown -R redis:redis /data

# Copy production configuration and scripts
COPY redis.conf /usr/local/etc/redis/redis.conf
COPY entrypoint.sh /entrypoint.sh
COPY benchmark.sh /usr/local/bin/benchmark.sh

RUN chmod +x /entrypoint.sh /usr/local/bin/benchmark.sh

# Persist cache data to disk
VOLUME ["/data"]
WORKDIR /data

# Standard Redis communication port
EXPOSE 6379

# Health probe using native redis-cli
HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=3 \
    CMD redis-cli ping | grep -q PONG || exit 1

ENTRYPOINT ["/entrypoint.sh"]
