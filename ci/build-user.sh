#!/bin/bash
set -euo pipefail
cd /build
readarray -t metadata < <(python3 -c 'import json; m=json.load(open("provenance.json")); print("\n".join(m[k] for k in ("version", "pkgrel", "source_sha256", "source_date_epoch", "package_name")))')
export FACELOCK_PACKAGE_VERSION="${metadata[0]}"
export FACELOCK_SOURCE_SHA256="${metadata[2]}"
export SOURCE_DATE_EPOCH="${metadata[3]}"
export FACELOCK_DEB_PACKAGE="${metadata[4]}"
[[ $FACELOCK_DEB_PACKAGE == facelock || $FACELOCK_DEB_PACKAGE == facelock-nightly ]]
printf '%s  facelock-source.tar\n' "$FACELOCK_SOURCE_SHA256" | sha256sum -c -
mkdir deb
tar -xf facelock-source.tar -C deb
cd /build/deb/facelock
if [[ ${metadata[4]} == facelock-nightly ]]; then
  sed -i -e 's/^Package: facelock$/Package: facelock-nightly/' \
    -e 's/^Conflicts: facelock-nightly$/Provides: facelock\nConflicts: facelock/' \
    -e 's/^Replaces: facelock-nightly$/Replaces: facelock/' debian/control
  cp debian/postinst debian/facelock-nightly.postinst
fi
changes=$(python3 -c 'import json; m=json.load(open("/build/provenance.json")); print("; ".join(c["subject"] for c in m["changes"]) or "No source changes since the previous release.")')
DEBFULLNAME=sppidy DEBEMAIL=sppidytg@gmail.com dch --newversion "${metadata[0]}-${metadata[1]}" \
  --distribution unstable "$changes"
dpkg-buildpackage -b -us -uc
mv /build/deb/*.deb /build/
