#!/usr/bin/env bash
# ==============================================================================
# Container Entrypoint & Process Supervisor for NGINX + Python Async Backend
# ==============================================================================
set -eo pipefail

echo "=========================================================="
echo " Starting Production API Gateway & Microservices Engine   "
echo "=========================================================="

# Calculate worker processes
NUM_CPUS=$(nproc)
UVICORN_WORKERS=${UVICORN_WORKERS:-$(( NUM_CPUS > 4 ? 4 : NUM_CPUS ))}
BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"

echo "[Init] Detected ${NUM_CPUS} logical CPUs."
echo "[Init] Launching Uvicorn with ${UVICORN_WORKERS} async worker processes."

# Signal handling for graceful shutdown
cleanup() {
    echo ""
    echo "[Shutdown] Caught termination signal! Initiating graceful teardown..."
    
    if [ -n "${NGINX_PID:-}" ] && kill -0 "${NGINX_PID}" 2>/dev/null; then
        echo "[Shutdown] Stopping NGINX gracefully..."
        nginx -s quit 2>/dev/null || kill -QUIT "${NGINX_PID}" 2>/dev/null || true
    fi

    if [ -n "${UVICORN_PID:-}" ] && kill -0 "${UVICORN_PID}" 2>/dev/null; then
        echo "[Shutdown] Terminating Uvicorn backend workers..."
        kill -TERM "${UVICORN_PID}" 2>/dev/null || true
    fi

    # Wait for processes to exit
    wait "${NGINX_PID:-}" 2>/dev/null || true
    wait "${UVICORN_PID:-}" 2>/dev/null || true
    echo "[Shutdown] Teardown complete. Exiting."
    exit 0
}

trap cleanup SIGINT SIGTERM SIGHUP

# 1. Validate NGINX Configuration
echo "[Init] Validating NGINX configuration..."
nginx -t

# 2. Launch FastAPI / Uvicorn Backend
echo "[Init] Starting async microservice backend on ${BACKEND_HOST}:${BACKEND_PORT}..."
/opt/venv/bin/uvicorn app.main:app \
    --host "${BACKEND_HOST}" \
    --port "${BACKEND_PORT}" \
    --workers "${UVICORN_WORKERS}" \
    --loop uvloop \
    --http httptools \
    --no-access-log &
UVICORN_PID=$!

# 3. Wait for backend readiness probe
echo "[Init] Waiting for backend to become ready..."
MAX_ATTEMPTS=30
ATTEMPT=0
READY=0
while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if curl -s -f "http://${BACKEND_HOST}:${BACKEND_PORT}/health" > /dev/null 2>&1; then
        READY=1
        break
    fi
    ATTEMPT=$(( ATTEMPT + 1 ))
    sleep 0.2
done

if [ $READY -ne 1 ]; then
    echo "[Error] Backend failed to become healthy within 6 seconds. Aborting."
    kill -9 "${UVICORN_PID}" 2>/dev/null || true
    exit 1
fi
echo "[Init] Backend is UP and reporting healthy."

# 4. Start NGINX
echo "[Init] Starting NGINX reverse proxy on port 80..."
nginx -g "daemon off;" &
NGINX_PID=$!

echo "=========================================================="
echo " API Gateway is LIVE: listening on :80                    "
echo " Uvicorn Backend: listening on 127.0.0.1:8000             "
echo " Trapping SIGINT/SIGTERM for zero-downtime shutdown       "
echo "=========================================================="

# 5. Process Supervisor Loop
while true; do
    if ! kill -0 "${UVICORN_PID}" 2>/dev/null; then
        echo "[Fatal] Uvicorn backend exited unexpectedly!"
        cleanup
        exit 1
    fi
    if ! kill -0 "${NGINX_PID}" 2>/dev/null; then
        echo "[Fatal] NGINX process exited unexpectedly!"
        cleanup
        exit 1
    fi
    sleep 1
done
