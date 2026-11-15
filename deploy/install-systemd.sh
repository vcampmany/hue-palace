#!/usr/bin/env bash
# Install (or upgrade) hue-palace as a systemd service, without Docker.
# Run on the Pi from a checkout of the repo:  sudo deploy/install-systemd.sh
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
cd "$(dirname "$0")/.."

apt-get install -y --no-install-recommends python3-venv python3-dev gcc linux-libc-dev

id hue-palace &>/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin hue-palace

python3 -m venv /opt/hue-palace/venv
/opt/hue-palace/venv/bin/pip install --upgrade pip
/opt/hue-palace/venv/bin/pip install .

install -d -m 750 -o root -g hue-palace /etc/hue-palace
if [[ ! -f /etc/hue-palace/config.yaml ]]; then
  sed 's#/config/appkey#/etc/hue-palace/appkey#' config.example.yaml > /etc/hue-palace/config.yaml
  chgrp hue-palace /etc/hue-palace/config.yaml && chmod 640 /etc/hue-palace/config.yaml
  echo "Created /etc/hue-palace/config.yaml from the example; edit it."
fi
ln -sf /opt/hue-palace/venv/bin/hue-palace /usr/local/bin/hue-palace

install -m 644 deploy/hue-palace.service /etc/systemd/system/hue-palace.service
systemctl daemon-reload
systemctl enable hue-palace
systemctl restart hue-palace
echo "Done. Logs: journalctl -u hue-palace -f"
