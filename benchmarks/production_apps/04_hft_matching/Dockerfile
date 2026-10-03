# syntax=docker/dockerfile:1
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY matching_engine.c .
RUN gcc -Wall -Wextra -Werror -O3 -march=x86-64-v3 matching_engine.c -o matching_engine -pthread

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /src/matching_engine /usr/local/bin/matching_engine

RUN mkdir -p /results

# Default to 250,000 synthetic market orders
ENTRYPOINT ["/usr/local/bin/matching_engine"]
CMD ["-n", "250000", "-j", "/results/hft_matching.json"]
