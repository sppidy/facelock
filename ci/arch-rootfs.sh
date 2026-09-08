#!/bin/bash
# Verify the official Arch Linux ARM rootfs before importing it as a builder.
set -euo pipefail
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
# The os.archlinuxarm.org alias presents a mismatched TLS certificate.
# Use an official mirror with valid HTTPS for both the rootfs and signature.
base=https://ca.us.mirror.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz
fingerprint=68B3537F39A313B3E574D06777193F152BDBE6A6
rootfs_sha256=42a4eeaa038994ffd31fa173256ef2f0ef511358eeb41b9ea1f8626391b9b319
curl --fail --location --retry 3 "$base" -o "$work/rootfs.tar.gz"
curl --fail --location --retry 3 "$base.sig" -o "$work/rootfs.tar.gz.sig"
printf '%s  %s\n' "$rootfs_sha256" "$work/rootfs.tar.gz" | sha256sum -c -
mkdir -m700 "$work/gnupg"
gpg --homedir "$work/gnupg" --batch --keyserver hkps://keyserver.ubuntu.com --recv-keys "$fingerprint"
gpg --homedir "$work/gnupg" --batch --status-fd=1 --verify "$work/rootfs.tar.gz.sig" "$work/rootfs.tar.gz" > "$work/status"
grep -q "VALIDSIG $fingerprint " "$work/status"
printf '%s  ArchLinuxARM-aarch64-rootfs.tar.gz\n' "$rootfs_sha256" > dist/arch-rootfs-sha256.txt
docker import "$work/rootfs.tar.gz" facelock-arch-builder
