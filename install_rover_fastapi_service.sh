#!/usr/bin/env bash
set -e

SERVICE_NAME="rover-fastapi.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SOURCE="$SCRIPT_DIR/$SERVICE_NAME"
SERVICE_TARGET="/etc/systemd/system/$SERVICE_NAME"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with: sudo ./install_rover_fastapi_service.sh"
  exit 1
fi

if [ ! -f "$SERVICE_SOURCE" ]; then
  echo "Missing file: $SERVICE_SOURCE"
  exit 1
fi

cp "$SERVICE_SOURCE" "$SERVICE_TARGET"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "Installed and started $SERVICE_NAME"
echo "Status: systemctl status $SERVICE_NAME"