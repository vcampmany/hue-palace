#!/usr/bin/env bash
# Build the arm64 image on this machine and load it on the Pi over SSH.
# Usage: deploy/push-to-pi.sh pi@hue-palace.local [remote-dir]
# Needs docker buildx with arm64 emulation here (docker run --privileged --rm tonistiigi/binfmt --install arm64)
# and docker + the compose plugin on the Pi.
set -euo pipefail

target=${1:?usage: $0 user@pi-host [remote-dir]}
remote_dir=${2:-hue-palace}
cd "$(dirname "$0")/.."

docker buildx build --platform linux/arm64 -t hue-palace:latest --output type=docker,dest=- . \
  | ssh "$target" docker load

ssh "$target" "mkdir -p '$remote_dir/config'"
scp compose.yaml "$target:$remote_dir/compose.yaml"
ssh "$target" "cd '$remote_dir' && test -f config/config.yaml \
  && docker compose up -d --no-build \
  || echo 'Now create $remote_dir/config/config.yaml and appkey on the Pi, then: docker compose up -d --no-build'"
