#!/usr/bin/env bash
# ==============================================================================
# Container Entrypoint & Kernel Advisor for Production Redis Cache
# ==============================================================================
set -eo pipefail

echo "=========================================================="
echo " Starting Production In-Memory Redis Cache Engine         "
echo "=========================================================="

CONFIG_FILE="/usr/local/etc/redis/redis.conf"
DATA_DIR="/data"

# Ensure data directory exists and has proper permissions
mkdir -p "${DATA_DIR}"
chown -R redis:redis "${DATA_DIR}" 2>/dev/null || true

# Kernel Configuration Check & Advisory
echo "[SysCheck] Verifying kernel parameters for high-throughput in-memory caching..."

# 1. Overcommit Memory Check
if [ -f /proc/sys/vm/overcommit_memory ]; then
    OVERCOMMIT=$(cat /proc/sys/vm/overcommit_memory)
    if [ "$OVERCOMMIT" -ne 1 ]; then
        echo "  [ADVISORY] /proc/sys/vm/overcommit_memory is $OVERCOMMIT (recommended: 1)."
        echo "             To prevent BGSAVE fork failures under high memory pressure, run:"
        echo "             sysctl vm.overcommit_memory=1"
    else
        echo "  [OK] vm.overcommit_memory = 1"
    fi
fi

# 2. SOMAXCONN Check
if [ -f /proc/sys/net/core/somaxconn ]; then
    SOMAXCONN=$(cat /proc/sys/net/core/somaxconn)
    if [ "$SOMAXCONN" -lt 65535 ]; then
        echo "  [ADVISORY] net.core.somaxconn is $SOMAXCONN (recommended: 65535)."
        echo "             To avoid dropped SYN packets during pipelining bursts, run:"
        echo "             sysctl -w net.core.somaxconn=65535"
    else
        echo "  [OK] net.core.somaxconn = $SOMAXCONN"
    fi
fi

# 3. Transparent Huge Pages (THP) Check
if [ -f /sys/kernel/mm/transparent_hugepage/enabled ]; then
    THP_STATUS=$(cat /sys/kernel/mm/transparent_hugepage/enabled)
    if echo "$THP_STATUS" | grep -q '\[always\]'; then
        echo "  [ADVISORY] Transparent Huge Pages (THP) is enabled."
        echo "             THP can induce latency spikes during Redis background copy-on-write."
        echo "             Recommended: echo never > /sys/kernel/mm/transparent_hugepage/enabled"
    else
        echo "  [OK] Transparent Huge Pages (THP) disabled or set to madvise."
    fi
fi

# Trap shutdown signals
cleanup() {
    echo ""
    echo "[Shutdown] Caught termination signal! Performing clean Redis shutdown..."
    if [ -n "${REDIS_PID:-}" ] && kill -0 "${REDIS_PID}" 2>/dev/null; then
        redis-cli -p 6379 shutdown save 2>/dev/null || kill -TERM "${REDIS_PID}" 2>/dev/null || true
        wait "${REDIS_PID}" 2>/dev/null || true
    fi
    echo "[Shutdown] Redis successfully stopped. Exiting."
    exit 0
}

trap cleanup SIGINT SIGTERM SIGHUP

echo "[Init] Launching redis-server with production configuration: ${CONFIG_FILE}"

if command -v gosu > /dev/null 2>&1; then
    exec gosu redis redis-server "${CONFIG_FILE}" "$@"
elif [ -x /usr/bin/setpriv ]; then
    exec /usr/bin/setpriv --reuid redis --regid redis --clear-groups redis-server "${CONFIG_FILE}" "$@"
else
    exec redis-server "${CONFIG_FILE}" "$@"
fi
