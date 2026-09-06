#!/bin/sh
# pam_exec entry for face-unlock. Any failure -> nonzero -> PAM moves on to
# the password module. Never blocks login.
CFG=/etc/facelock/config.yaml
LOG=/var/lib/facelock/pam.log
# resolve invoking user without trusting a single variable. pam_exec hands
# children a scrubbed env, so also try the parent's real UID (sudo/hyprlock
# run with the invoking user's ruid; login managers keep password fallback).
CANDIDATE="${PAM_USER:-}"
[ -n "$CANDIDATE" ] || CANDIDATE="${PAM_RUSER:-}"
[ -n "$CANDIDATE" ] || CANDIDATE="${SUDO_USER:-}"
if [ -z "$CANDIDATE" ] && [ -r "/proc/$PPID/status" ]; then
    RUID=$(awk '/^Uid:/{print $2}' "/proc/$PPID/status" 2>/dev/null)
    [ -n "$RUID" ] && [ "$RUID" != "0" ] && \
        CANDIDATE="$(id -nu "$RUID" 2>/dev/null)"
fi
[ -n "$CANDIDATE" ] || CANDIDATE="$(logname 2>/dev/null)"
[ -n "$CANDIDATE" ] || exit 1
WANT=$(grep -E '^\s*user:' "$CFG" 2>/dev/null | head -1 | awk '{print $2}' | tr -d '"')
[ -n "$WANT" ] && [ "$CANDIDATE" = "$WANT" ] || exit 1
# relocatable: works from /usr/local (dev install) and /usr (packages)
LIBDIR=$(dirname "$(readlink -f "$0")")
exec "$LIBDIR/../bin/facelock-run" "$LIBDIR/verify.py" \
  --quiet --user "$CANDIDATE" >>"$LOG" 2>&1
