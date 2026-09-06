#!/usr/bin/env bash
# List libcamera devices with selectable names for config.yaml
set -euo pipefail
timeout 8 gst-device-monitor-1.0 Video/Source 2>/dev/null | grep -E '^\s+name|Model|Location' || true
echo '--- cam -l ---'
cam -l 2>&1 | grep -vE 'INFO|WARN' || true
