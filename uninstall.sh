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
sudo rm -rf /usr/local/lib/facelock /etc/udev/rules.d/99-facelock-ir-led.rules
sudo rm -f /usr/local/bin/facelock-run /usr/local/bin/facelock-detect \
  /usr/local/bin/facelock-pam-enable
sudo udevadm control --reload-rules || true
echo 'removed (config in /etc/facelock kept for reference)'
