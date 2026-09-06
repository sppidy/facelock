#!/bin/sh
# pam_exec entry for face-unlock. Any failure -> nonzero -> PAM moves on to
# the password module. Never blocks login.
CFG=/etc/facelock/config.yaml
LOG=/var/lib/facelock/pam.log
[ -n "$PAM_USER" ] || exit 1
WANT=$(grep -E '^\s*user:' "$CFG" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"')
[ -n "$WANT" ] && [ "$PAM_USER" = "$WANT" ] || exit 1
exec /usr/local/bin/facelock-run /usr/local/lib/facelock/verify.py \
  --quiet --user "$PAM_USER" >>"$LOG" 2>&1
