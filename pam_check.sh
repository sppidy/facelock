#!/bin/sh
# PAM supplies the account being authenticated. Never infer a different one.
set -eu
[ "${PAM_TYPE:-}" = auth ] && [ -n "${PAM_USER:-}" ] || exit 1
SELF=$(/usr/bin/readlink -f "$0")
LIBDIR=${SELF%/*}
exec "$LIBDIR/../../bin/facelock-run" "$LIBDIR/verify.py" --pam --quiet \
  >/dev/null 2>&1
