#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
install -m644 /checkout/ci/debian.sources /etc/apt/sources.list.d/debian.sources
apt-get update
apt-get install -y --no-install-recommends makepkg pacman-package-manager \
  build-essential fakeroot debhelper devscripts python3 git zstd ca-certificates libarchive-tools dh-python meson ninja-build pkg-config \
  python3-dev python3-jinja2 python3-yaml python3-ply pybind11-dev patchelf \
  libyaml-dev libgnutls28-dev libevent-dev libudev-dev libssl-dev
test "$(dpkg --print-architecture)" = arm64
useradd -m builder
install -d /var/lib/pacman/local
install -d -o builder -g builder /build
cp /out/facelock-source.tar /out/provenance.json /build/
cp /checkout/ci/build-user.sh /build/
chown builder:builder /build/*
runuser -u builder -- bash /build/build-user.sh
package_name=$(python3 -c 'import json; print(json.load(open("/build/provenance.json"))["package_name"])')
pam_before=$(find /etc/pam.d -type f -exec sha256sum {} + | sort | sha256sum)
dpkg --force-depends -i /build/*.deb
test "$(dpkg-query -W -f='${binary:Package}' "$package_name")" = "$package_name"
test -x /usr/bin/facelock-run
test "$pam_before" = "$(find /etc/pam.d -type f -exec sha256sum {} + | sort | sha256sum)"
dpkg --force-depends -r "$package_name"
test ! -e /usr/bin/facelock-run
cp /build/*.deb /out/
set -x
repo_work=/repo
rm -rf "$repo_work" && install -d -o builder -g builder "$repo_work"
cp /out/*.pkg.tar.zst "$repo_work/"
chown builder:builder "$repo_work"/*
runuser -u builder -- bash -c 'cd /repo && repo-add facelock.db.tar.zst *.pkg.tar.zst && rm -f facelock.db facelock.files && cp facelock.db.tar.zst facelock.db && cp facelock.files.tar.zst facelock.files'
set +x
cp "$repo_work"/facelock.db* "$repo_work"/facelock.files* /out/
ls /out/facelock.db* /out/facelock.files*
python3 /checkout/ci/inspect.py /out
dpkg-query -W > /out/build-packages.txt
cd /out
release_files=( *.deb *.pkg.tar.zst libcamera-patched-source.tar.gz )
sha256sum -- "${release_files[@]}" > SHA256SUMS
sha256sum -c SHA256SUMS
