#!/bin/bash
set -euo pipefail
cd /build
readarray -t metadata < <(python3 -c 'import json; m=json.load(open("provenance.json")); print("\n".join(m[k] for k in ("version", "source_sha256", "source_date_epoch")))')
export FACELOCK_PACKAGE_VERSION="${metadata[0]}" FACELOCK_SOURCE_SHA256="${metadata[1]}" SOURCE_DATE_EPOCH="${metadata[2]}"
export FACELOCK_SOURCE_ARCHIVE=facelock-source.tar
printf '%s  facelock-source.tar\n' "$FACELOCK_SOURCE_SHA256" | sha256sum -c -
tar -xf facelock-source.tar
cp facelock/PKGBUILD facelock/facelock.install .
# Build dependencies are installed explicitly above; no system camera package
# is installed in this builder, so the private-runtime smoke test catches leaks.
PKGEXT=.pkg.tar.zst makepkg --nodeps --noconfirm

mkdir packaged-smoke
bsdtar -xf ./*.pkg.tar.zst -C packaged-smoke
python3 -c 'import runpy; runpy.run_path("facelock/camera-runtime/build.py")["smoke"]("packaged-smoke/usr/lib/facelock/camera")'
