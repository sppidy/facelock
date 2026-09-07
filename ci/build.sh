#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends makepkg pacman-package-manager \
  build-essential fakeroot debhelper devscripts python3 git zstd ca-certificates libarchive-tools
test "$(dpkg --print-architecture)" = arm64
useradd -m builder
install -d /var/lib/pacman/local
install -d -o builder -g builder /build
cp /out/facelock-source.tar /out/provenance.json /build/
cp /checkout/ci/build-user.sh /build/
chown builder:builder /build/*
runuser -u builder -- bash /build/build-user.sh
cp /build/arch/*.pkg.tar.zst /build/*.deb /out/
set -x
repo_work=/repo
rm -rf "$repo_work" && install -d -o builder -g builder "$repo_work"
cp /out/*.pkg.tar.zst "$repo_work/"
chown builder:builder "$repo_work"/*
runuser -u builder -- bash -c 'cd /repo && repo-add facelock.db.tar.zst *.pkg.tar.zst'
set +x
cp "$repo_work"/facelock.db* "$repo_work"/facelock.files* /out/
ls /out/facelock.db* /out/facelock.files*
python3 /checkout/ci/inspect.py /out
dpkg-query -W > /out/build-packages.txt
cd /out
sha256sum -- * > SHA256SUMS
sha256sum -c SHA256SUMS
