#!/bin/sh
# pam_exec entry for face-unlock. Any failure -> nonzero -> PAM moves on to
# the password module. Never blocks login.
echo "RAN $(date +%T) PAM_USER=${PAM_USER:-empty}" >> /tmp/facelock-dbg.log 2>&1 || true
env | sort >> /tmp/facelock-env.log 2>&1 || true
CFG=/etc/facelock/config.yaml
LOG=/var/lib/facelock/pam.log
DBG=/tmp/facelock-dbg.log
{
  echo "--- $(date +%T) service=${PAM_SERVICE:-?} PU=${PAM_USER:--} PR=${PAM_RUSER:-=} SU=${SUDO_USER:-=} PPID=$PPID"
  echo "PPID_UID=$(awk '/^Uid:/{print $2}' /proc/$PPID/status 2>/dev/null)"
  id
  grep -E "^(Cap|NoNewPrivs)" /proc/self/status
  readlink /proc/self/ns/user
} >> $DBG 2>&1 || true
# debug: record what PAM actually exports (root-only file)
{ echo "--- $(date +%T) PAM_SERVICE=$PAM_SERVICE PAM_TYPE=$PAM_TYPE"; env | grep -E "^(PAM_|SUDO_|USER=|LOGNAME=)" | sort; } >> /var/lib/facelock/env.log 2>&1 || true
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
echo "CANDIDATE=${CANDIDATE:-empty} WANT=${WANT:-empty}" >> $DBG 2>&1 || true
[ -n "$WANT" ] && [ "$CANDIDATE" = "$WANT" ] || { echo "USER-MISMATCH exit 1" >> $DBG; exit 1; }
echo "EXEC verify" >> $DBG 2>&1 || true
echo "which-python=$(command -v python) wrapper=$0 args=$*" >> $DBG 2>&1 || true
OUT=$(/usr/local/bin/facelock-run /usr/local/lib/facelock/verify.py \
  --quiet --user "$CANDIDATE" 2>&1)
RC=$?
echo "verify rc=$RC outlen=${#OUT} marker-after-call" >> $DBG 2>&1 || true
echo "$OUT" >>"$LOG" 2>&1 && echo "log-write-ok" >> $DBG 2>&1 || \
  echo "LOG-WRITE-FAILED" >> $DBG 2>&1
echo "$OUT" >> $DBG 2>&1 || true
exit $RC
