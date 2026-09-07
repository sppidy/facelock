# Maintainer: sppidy
# AUR uses the immutable packaging release tag; CI supplies a checksummed
# archive of the exact checkout, without fetching an older upstream tag.
_giturl="https://github.com/sppidy/facelock.git"
pkgname=facelock
pkgver=0.1.0
pkgrel=3
_gittag="v${pkgver}-${pkgrel}"
pkgdesc="Howdy-style face login rebuilt for libcamera/ISP and UVC cameras on ARM laptops"
arch=('any')
url="https://github.com/sppidy/facelock"
license=('MIT')
depends=('python' 'python-opencv' 'python-numpy' 'python-yaml'
         'gstreamer' 'gst-plugins-base' 'v4l-utils' 'libcamera' 'pam')
optdepends=('python-libcamera: concurrent dual-sensor capture'
            'gst-plugin-libcamera: libcamera cameras over GStreamer'
            'gst-plugins-good: UVC webcams over GStreamer (v4l2src)'
            'hyprlock: face unlock on the Hyprland lock screen')
makedepends=('git' 'tar')
backup=('etc/facelock/config.yaml')
options=('!debug') # Python and shell only; no separate debug package.
if [[ -n ${FACELOCK_SOURCE_ARCHIVE:-} ]]; then
  : "${FACELOCK_SOURCE_SHA256:?local source requires SHA256}"
  source=("$FACELOCK_SOURCE_ARCHIVE")
  sha256sums=("$FACELOCK_SOURCE_SHA256")
  pkgver=${FACELOCK_PACKAGE_VERSION:-$pkgver}
else
  source=("git+${_giturl}#tag=${_gittag}")
  sha256sums=('SKIP')
fi
install=facelock.install

package() {
  cd "$pkgname"
  install -Dm755 facelock-run "$pkgdir/usr/bin/facelock-run"
  install -Dm755 facelock-detect "$pkgdir/usr/bin/facelock-detect"
  install -Dm755 facelock-pam-enable "$pkgdir/usr/bin/facelock-pam-enable"
  install -Dm755 setup_models.py "$pkgdir/usr/lib/facelock/setup_models.py"
  install -Dm755 enroll.py "$pkgdir/usr/lib/facelock/enroll.py"
  install -Dm755 verify.py "$pkgdir/usr/lib/facelock/verify.py"
  install -Dm755 pam_check.sh "$pkgdir/usr/lib/facelock/pam_check.sh"
  install -Dm644 facelock/*.py -t "$pkgdir/usr/lib/facelock/facelock/"
  install -Dm644 profiles/*.yaml -t "$pkgdir/usr/share/facelock/profiles/"
  install -Dm644 config.yaml "$pkgdir/usr/share/doc/facelock/config.example.yaml"
  install -Dm644 README.md "$pkgdir/usr/share/doc/facelock/README.md"
  install -Dm644 99-facelock-ir-led.rules \
    "$pkgdir/usr/lib/udev/rules.d/99-facelock-ir-led.rules"
  install -Dm644 config.yaml "$pkgdir/etc/facelock/config.yaml"
  # Keep biometric storage private to root; do not embed a build-host video GID.
  install -dm770 "$pkgdir/var/lib/facelock"
}
