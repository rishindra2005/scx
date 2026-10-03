# syntax=docker/dockerfile:1
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY audio_dsp_sim.c .
RUN gcc -Wall -Wextra -Werror -O3 -march=x86-64-v3 audio_dsp_sim.c -o audio_dsp_sim -lm -lpthread

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcap2-bin \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /src/audio_dsp_sim /usr/local/bin/audio_dsp_sim

RUN mkdir -p /results

# Default to 30,000 frames (~40s of 48kHz audio at 64 samples buffer)
ENTRYPOINT ["/usr/local/bin/audio_dsp_sim"]
CMD ["-n", "30000", "-j", "/results/audio_dsp.json"]
