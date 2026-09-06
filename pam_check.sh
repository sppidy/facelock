#!/bin/sh
# pam_exec entry for face-unlock. Any failure -> nonzero -> PAM moves on to
# the password module. Never blocks login.
echo "RAN $(date +%T) PAM_USER=${PAM_USER:-empty}" >> /tmp/facelock-dbg.log 2>&1 || true
CFG=/etc/facelock/config.yaml
LOG=/var/lib/facelock/pam.log
# debug: record what PAM actually exports (root-only file)
{ echo "--- $(date +%T) PAM_SERVICE=$PAM_SERVICE PAM_TYPE=$PAM_TYPE"; env | grep -E "^(PAM_|SUDO_|USER=|LOGNAME=)" | sort; } >> /var/lib/facelock/env.log 2>&1 || true
# resolve invoking user without trusting a single variable
CANDIDATE="${PAM_USER:-}"
[ -n "$CANDIDATE" ] || CANDIDATE="${PAM_RUSER:-}"
[ -n "$CANDIDATE" ] || CANDIDATE="${SUDO_USER:-}"
[ -n "$CANDIDATE" ] || CANDIDATE="$(logname 2>/dev/null)"
[ -n "$CANDIDATE" ] || exit 1
WANT=$(grep -E '^\s*user:' "$CFG" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"')
[ -n "$WANT" ] && [ "$CANDIDATE" = "$WANT" ] || exit 1
exec /usr/local/bin/facelock-run /usr/local/lib/facelock/verify.py \
  --quiet --user "$CANDIDATE" >>"$LOG" 2>&1
