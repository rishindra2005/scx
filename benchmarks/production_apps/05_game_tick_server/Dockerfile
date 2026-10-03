# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app
COPY game_server.py /app/game_server.py
RUN chmod +x /app/game_server.py && mkdir -p /results

# Default to 500 connected clients for 30 seconds at 120 FPS
ENTRYPOINT ["python3", "/app/game_server.py"]
CMD ["-c", "500", "-d", "30", "-j", "/results/game_server.json"]
