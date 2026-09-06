#!/bin/bash
set -euo pipefail
cd /build
readarray -t metadata < <(python3 -c 'import json; m=json.load(open("provenance.json")); print("\n".join(m[k] for k in ("version", "pkgrel", "source_sha256", "source_date_epoch")))')
export FACELOCK_PACKAGE_VERSION="${metadata[0]}"
export FACELOCK_SOURCE_SHA256="${metadata[2]}"
export SOURCE_DATE_EPOCH="${metadata[3]}"
printf '%s  facelock-source.tar\n' "$FACELOCK_SOURCE_SHA256" | sha256sum -c -
mkdir arch deb
tar -xf facelock-source.tar -C deb
cp deb/facelock/PKGBUILD deb/facelock/facelock.install arch/
cp facelock-source.tar arch/
export FACELOCK_SOURCE_ARCHIVE=facelock-source.tar
cd /build/arch
# Debian supplies makepkg, not Arch runtime dependencies. Nothing is compiled.
PKGEXT=.pkg.tar.zst makepkg --nodeps --noconfirm --clean
cd /build/deb/facelock
DEBFULLNAME=sppidy DEBEMAIL=sppidytg@gmail.com dch --newversion "${metadata[0]}-${metadata[1]}" \
  --distribution unstable 'Build from exact CI checkout; see provenance.json.'
dpkg-buildpackage -b -us -uc
mv /build/deb/*.deb /build/
