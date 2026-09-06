#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends makepkg pacman-package-manager \
  build-essential fakeroot debhelper devscripts python3 git zstd ca-certificates
test "$(dpkg --print-architecture)" = arm64
useradd -m builder
install -d -o builder -g builder /build
cp /out/facelock-source.tar /out/provenance.json /build/
cp /checkout/ci/build-user.sh /build/
chown builder:builder /build/*
runuser -u builder -- bash /build/build-user.sh
cp /build/arch/*.pkg.tar.zst /build/*.deb /out/
python3 /checkout/ci/inspect.py /out
dpkg-query -W > /out/build-packages.txt
cd /out
sha256sum -- * > SHA256SUMS
sha256sum -c SHA256SUMS
