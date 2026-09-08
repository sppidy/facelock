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
cp /build/*.pkg.tar.zst /out/
cp /build/src/libcamera-patched-source.tar.gz /out/libcamera-patched-source.tar.gz
pacman -Q > /out/arch-build-packages.txt
