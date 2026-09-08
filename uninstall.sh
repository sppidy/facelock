#!/usr/bin/env bash
# Restore PAM backups, remove installed files. Keeps models + enrollments
# unless --purge is given.
set -euo pipefail
if [ "${1:-}" = "--purge" ]; then
  sudo rm -rf /var/lib/facelock /usr/local/share/facelock
fi
for svc in greetd login sudo hyprlock; do
  b="/etc/facelock/pam-backup/$svc"
  [ -f "$b" ] && sudo cp "$b" "/etc/pam.d/$svc" && echo "$svc: restored"
done
sudo systemctl disable --now facelock-auth.socket || true
sudo rm -rf /usr/local/lib/facelock /etc/udev/rules.d/99-facelock-ir-led.rules
sudo rm -f /usr/local/bin/facelock-run /usr/local/bin/facelock-detect \
  /usr/local/bin/facelock-pam-enable /usr/local/bin/facelock-feedback /usr/local/bin/facelock-enroll \
  /usr/local/lib/systemd/user/facelock-feedback.service \
  /usr/local/bin/facelock-auth /usr/local/lib/systemd/system/facelock-auth.socket \
  /usr/local/lib/systemd/system/facelock-auth@.service \
  /usr/local/share/applications/io.github.sppidy.Facelock.desktop
sudo udevadm control --reload-rules || true
sudo systemctl daemon-reload
echo 'removed (config in /etc/facelock kept for reference)'
