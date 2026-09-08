#!/bin/bash
set -euo pipefail
pacman-key --init
pacman-key --populate archlinuxarm
pacman -Syu --disable-sandbox --noconfirm --needed base-devel git python python-numpy python-opencv \
 python-yaml python-jinja python-ply pybind11 meson ninja patchelf \
 libyaml gnutls libevent systemd-libs openssl tar zstd
useradd -m builder
install -d -o builder -g builder /build
cp /out/facelock-source.tar /out/provenance.json /build/
chown builder:builder /build/*
runuser -u builder -- bash /checkout/ci/build-arch-user.sh
package_name=$(python3 -c 'import json; print(json.load(open("/build/provenance.json"))["package_name"])')
pam_before=$(find /etc/pam.d -type f -exec sha256sum {} + | sort | sha256sum)
pacman -Udd --noconfirm /build/*.pkg.tar.zst
pacman -Q "$package_name"
test -x /usr/bin/facelock-run
test "$pam_before" = "$(find /etc/pam.d -type f -exec sha256sum {} + | sort | sha256sum)"
pacman -Rdd --noconfirm "$package_name"
test ! -e /usr/bin/facelock-run
cp /build/*.pkg.tar.zst /out/
cp /build/src/libcamera-patched-source.tar.gz /out/libcamera-patched-source.tar.gz
pacman -Q > /out/arch-build-packages.txt
