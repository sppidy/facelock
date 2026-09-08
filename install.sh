#!/usr/bin/env bash
# face-unlock installer (dev-tree layout under /usr/local).
# Run as your user (uses sudo internally).
# Backs up every PAM file it touches. Keeps password fallback (sufficient).
# Usage: ./install.sh [--profile NAME] [--no-pam]
#   --profile NAME  write profiles/NAME.yaml to /etc/facelock/config.yaml
#                   (backs up any existing config first)
#   --no-pam        skip PAM wiring (do it later with facelock-pam-enable)
set -euo pipefail
cd "$(dirname "$0")"
PROFILE=""
WIRE_PAM=0
for arg in "$@"; do
  case "$arg" in
    --profile=*) PROFILE="${arg#--profile=}" ;;
    --no-pam) WIRE_PAM=0 ;;
    --pam) WIRE_PAM=1 ;;
    *) echo "unknown arg: $arg"; exit 1 ;;
  esac
done

echo '== 1/5 deps =='
sudo pacman -S --needed --noconfirm \
  python-opencv python-numpy python-yaml gst-plugins-base gstreamer libcamera v4l-utils

echo '== 2/5 files =='
sudo mkdir -p /usr/local/lib/facelock /usr/local/share/facelock \
  /var/lib/facelock /etc/facelock /etc/facelock/pam-backup
# replace (not merge) so stale files can never survive an install
sudo rm -rf /usr/local/lib/facelock/facelock \
  /usr/local/lib/facelock/__pycache__
sudo cp -r facelock /usr/local/lib/facelock/facelock
sudo cp enroll.py verify.py runner.py diagnose.py setup_models.py pam_check.sh dual_test.py \
  ir_check.py /usr/local/lib/facelock/
sudo sha256sum /usr/local/lib/facelock/verify.py \
  /usr/local/lib/facelock/facelock/dual_capture.py | head -4
sudo cp facelock-run facelock-detect facelock-pam-enable /usr/local/bin/
sudo chmod 755 /usr/local/bin/facelock-run /usr/local/bin/facelock-detect \
  /usr/local/bin/facelock-pam-enable
sudo mkdir -p /usr/local/share/facelock/profiles /usr/share/facelock/tuning/simple
sudo cp tuning/simple/*.yaml /usr/share/facelock/tuning/simple/
sudo cp profiles/*.yaml /usr/local/share/facelock/profiles/
if [ -n "$PROFILE" ]; then
  [ -f "profiles/$PROFILE.yaml" ] || { echo "no such profile: $PROFILE"; exit 1; }
  [ -f /etc/facelock/config.yaml ] && \
    sudo cp /etc/facelock/config.yaml "/etc/facelock/config.yaml.bak.$(date +%s)"
  sudo cp "profiles/$PROFILE.yaml" /etc/facelock/config.yaml
  echo "installed profile $PROFILE (edit pam.user inside for your login)"
elif [ ! -f /etc/facelock/config.yaml ]; then
  sudo cp config.yaml /etc/facelock/config.yaml
fi
sudo chmod 755 /usr/local/lib/facelock/*.py \
  /usr/local/lib/facelock/pam_check.sh
# Private storage; legacy .npz files remain unused until fresh enrollment.
sudo chown root:root /var/lib/facelock
sudo chmod 700 /var/lib/facelock
sudo chmod 600 /var/lib/facelock/*.log /var/lib/facelock/*.jsonl 2>/dev/null || true

echo '== 3/5 models =='
sudo python setup_models.py

echo '== 4/5 IR LED udev =='
sudo cp 99-facelock-ir-led.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=leds --action=change || true
# immediate effect for the already-present LED (rule covers future boots)
for attr in brightness flash_strobe flash_brightness flash_timeout; do
  sudo chgrp video "/sys/class/leds/ir:flash/$attr" 2>/dev/null || true
  sudo chmod 664 "/sys/class/leds/ir:flash/$attr" 2>/dev/null || true
done

echo '== 5/5 PAM (greetd login sudo hyprlock), backups in /etc/facelock/pam-backup =='
if [ "$WIRE_PAM" = 1 ]; then
  [ -f /usr/lib/security/pam_exec.so ] || {
    echo 'missing pam_exec.so, aborting PAM wiring'; exit 1; }
  sudo FACELOCK_PAM_CHECK=/usr/local/lib/facelock/pam_check.sh \
    /usr/local/bin/facelock-pam-enable greetd login sudo hyprlock
else
  echo 'skipped (--no-pam). Wire later: sudo facelock-pam-enable login sudo'
fi

echo
echo 'Next: sudo python /usr/local/lib/facelock/enroll.py --sensor both'
echo 'Then: sudo facelock-run /usr/local/lib/facelock/verify.py'
echo 'Test login/sudo from a SECOND session before logging out.'
