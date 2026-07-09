#!/usr/bin/env bash
# izar2mqtt installer: builds rtl_wmbus + wmbusmeters, installs the HA bridge,
# config and systemd service. Tested on Ubuntu 24.04/25.04. Run as root.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${BUILD_DIR:-/tmp/izar2mqtt-build}"

echo "==> Installing build & runtime dependencies (apt)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    git build-essential cmake pkg-config \
    rtl-sdr librtlsdr-dev libusb-1.0-0-dev \
    libxml2-dev mosquitto-clients python3

mkdir -p "$BUILD_DIR"

echo "==> Building rtl_wmbus"
if [ ! -d "$BUILD_DIR/rtl-wmbus" ]; then
    git clone --depth 1 https://github.com/xaelsouth/rtl-wmbus.git "$BUILD_DIR/rtl-wmbus"
fi
make -C "$BUILD_DIR/rtl-wmbus" -j"$(nproc)"
install -m755 "$BUILD_DIR/rtl-wmbus/build/rtl_wmbus" /usr/local/bin/rtl_wmbus

echo "==> Building wmbusmeters"
if [ ! -d "$BUILD_DIR/wmbusmeters" ]; then
    git clone --depth 1 https://github.com/wmbusmeters/wmbusmeters.git "$BUILD_DIR/wmbusmeters"
fi
( cd "$BUILD_DIR/wmbusmeters" && ./configure && make -j"$(nproc)" )
install -m755 "$BUILD_DIR/wmbusmeters/build/wmbusmeters" /usr/local/bin/wmbusmeters

echo "==> Installing bridge, config and service"
install -m755 "$REPO_DIR/bin/wmbus-ha-bridge.py" /usr/local/bin/wmbus-ha-bridge.py

mkdir -p /etc/wmbusmeters.d
[ -f /etc/wmbusmeters.conf ] || install -m644 "$REPO_DIR/config/wmbusmeters.conf.example" /etc/wmbusmeters.conf
[ -f /etc/wmbusmeters.d/AllIzar ] || install -m644 "$REPO_DIR/config/wmbusmeters.d/AllIzar.example" /etc/wmbusmeters.d/AllIzar

install -m644 "$REPO_DIR/systemd/wmbusmeters-izar2mqtt.service" /etc/systemd/system/wmbusmeters-izar2mqtt.service

cat <<'EOF'

==> Done. Next steps:
  1. Edit /etc/wmbusmeters.conf        (RTL device / frequency / gain / tuner flags)
  2. Edit the MQTT_* Environment lines in
     /etc/systemd/system/wmbusmeters-izar2mqtt.service
  3. Verify the dongle:                rtl_test -t
  4. Enable & start:
        systemctl daemon-reload
        systemctl enable --now wmbusmeters-izar2mqtt.service
  5. Watch it:                         journalctl -u wmbusmeters-izar2mqtt -f
Your meters appear in Home Assistant under Settings -> Devices -> MQTT.
EOF
