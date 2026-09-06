#!/usr/bin/env bash
# face-unlock installer. Run as your user (uses sudo internally).
# Backs up every PAM file it touches. Keeps password fallback (sufficient).
set -euo pipefail
cd "$(dirname "$0")"

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
sudo cp enroll.py verify.py setup_models.py pam_check.sh dual_test.py \
  ir_check.py /usr/local/lib/facelock/
sudo sha256sum /usr/local/lib/facelock/verify.py \
  /usr/local/lib/facelock/facelock/dual_capture.py | head -4
sudo cp facelock-run /usr/local/bin/facelock-run
sudo chmod 755 /usr/local/bin/facelock-run
[ -f /etc/facelock/config.yaml ] || sudo cp config.yaml /etc/facelock/config.yaml
sudo chmod 755 /usr/local/lib/facelock/*.py \
  /usr/local/lib/facelock/pam_check.sh
# store/log must be group-accessible: sudo runs PAM auth helpers as the
# invoking user (uid 1000, zero caps), not as root
sudo chgrp video /var/lib/facelock
sudo chmod 750 /var/lib/facelock
sudo touch /var/lib/facelock/pam.log /var/lib/facelock/env.log
sudo chgrp video /var/lib/facelock/pam.log /var/lib/facelock/env.log
sudo chmod 640 /var/lib/facelock/pam.log /var/lib/facelock/env.log
sudo chgrp video /var/lib/facelock/*.npz 2>/dev/null || true
sudo chmod 640 /var/lib/facelock/*.npz 2>/dev/null || true
sudo chmod 700 /var/lib/facelock

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

echo '== 5/5 PAM (login sudo hyprlock), backups in /etc/facelock/pam-backup =='
[ -f /usr/lib/security/pam_exec.so ] || {
  echo 'missing pam_exec.so, aborting PAM wiring'; exit 1; }
LINE='auth sufficient pam_exec.so /usr/local/lib/facelock/pam_check.sh'
for svc in login sudo hyprlock; do
  f="/etc/pam.d/$svc"
  [ -f "$f" ] || { echo "skip $svc (no $f)"; continue; }
  sudo cp -n "$f" "/etc/facelock/pam-backup/$svc" || true
  if sudo grep -qF "$LINE" "$f"; then echo "$svc: already wired"; continue; fi
  sudo awk -v line="$LINE" 'NR==1{print; print line; next}1' "$f" \
    | sudo tee "$f.new" >/dev/null
  sudo mv "$f.new" "$f"
  echo "$svc: wired"
done

echo
echo 'Next: sudo python /usr/local/lib/facelock/enroll.py --sensor both'
echo 'Then: python /usr/local/lib/facelock/verify.py'
echo 'Test login/sudo from a SECOND session before logging out.'
