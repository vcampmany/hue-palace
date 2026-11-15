# Multi-arch image; for the Pi Zero 2 W build with --platform linux/arm64.
FROM python:3.12-slim-bookworm AS build
# evdev ships as source only and needs a compiler plus the kernel input headers.
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev linux-libc-dev \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim-bookworm
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HUE_PALACE_CONFIG=/config/config.yaml
EXPOSE 8080
# Runs as root inside the container: it only gets /dev/input (see compose.yaml),
# and the host's `input` group id isn't known at build time.
ENTRYPOINT ["hue-palace"]
CMD ["run"]
