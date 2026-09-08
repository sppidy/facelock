# Maintainer: sppidy
# AUR uses the immutable packaging release tag; CI supplies a checksummed
# archive of the exact checkout, without fetching an older upstream tag.
_giturl="https://github.com/sppidy/facelock.git"
pkgname=${FACELOCK_PACKAGE_NAME:-facelock}
_source_dir=facelock
pkgver=0.3.0
pkgrel=2
_gittag="v${pkgver}-${pkgrel}"
pkgdesc="Howdy-style face login rebuilt for libcamera/ISP and UVC cameras on ARM laptops"
arch=('aarch64')
url="https://github.com/sppidy/facelock"
license=('MIT')
# Keep package metadata aligned with the compiled CPython binding.
depends=('python' 'python>=3.14' 'python<3.15' 'python-opencv' 'python-numpy' 'python-yaml'
         'gstreamer' 'gst-plugins-base' 'v4l-utils' 'pam' 'libyaml' 'gnutls' 'libevent' 'systemd-libs')
optdepends=('python-libcamera: optional system runtime for other camera profiles'
            'gst-plugin-libcamera: libcamera cameras over GStreamer'
            'gst-plugins-good: UVC webcams over GStreamer (v4l2src)'
            'hyprlock: face unlock on the Hyprland lock screen'
            'gtk4: graphical enrollment wizard'
            'python-gobject: graphical enrollment wizard'
            'polkit: authorize enrollment from the desktop')
makedepends=('git' 'tar' 'meson' 'ninja' 'pkgconf' 'python-jinja' 'python-yaml' 'python-ply' 'pybind11' 'patchelf' 'openssl')
backup=('etc/facelock/config.yaml')
if [[ $pkgname == facelock-nightly ]]; then
  provides=(facelock)
  conflicts=(facelock)
else
  conflicts=(facelock-nightly)
fi
options=('!debug') # Keep private runtime debug symbols out of a separate package.
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

build() {
  cd "$_source_dir"
  python3 -c 'import sys; assert sys.version_info[:2] == (3, 14), "Update the Arch Python ABI bounds before rebuilding"'
  python3 camera-runtime/build.py --work "$srcdir/camera-build" --output "$srcdir/camera-runtime"
}

package() {
  cd "$_source_dir"
  test -f "$srcdir/camera-runtime/manifest.json"
  install -dm755 "$pkgdir/usr/lib/facelock"
  cp -a "$srcdir/camera-runtime" "$pkgdir/usr/lib/facelock/camera"
  install -Dm755 facelock-run "$pkgdir/usr/bin/facelock-run"
  install -Dm755 facelock-detect "$pkgdir/usr/bin/facelock-detect"
  install -Dm755 facelock-pam-enable "$pkgdir/usr/bin/facelock-pam-enable"
  install -Dm755 facelock-feedback "$pkgdir/usr/bin/facelock-feedback"
  install -Dm755 facelock-auth "$pkgdir/usr/bin/facelock-auth"
  install -Dm644 systemd/facelock-auth.socket "$pkgdir/usr/lib/systemd/system/facelock-auth.socket"
  install -Dm644 systemd/facelock-auth@.service "$pkgdir/usr/lib/systemd/system/facelock-auth@.service"
  install -Dm755 facelock-enroll "$pkgdir/usr/bin/facelock-enroll"
  install -Dm644 systemd/facelock-feedback.service "$pkgdir/usr/lib/systemd/user/facelock-feedback.service"
  install -Dm644 desktop/io.github.sppidy.Facelock.desktop "$pkgdir/usr/share/applications/io.github.sppidy.Facelock.desktop"
  install -Dm755 setup_models.py "$pkgdir/usr/lib/facelock/setup_models.py"
  install -Dm755 enroll.py "$pkgdir/usr/lib/facelock/enroll.py"
  install -Dm755 runner.py "$pkgdir/usr/lib/facelock/runner.py"
  install -Dm755 diagnose.py "$pkgdir/usr/lib/facelock/diagnose.py"
  install -Dm755 calibrate.py "$pkgdir/usr/lib/facelock/calibrate.py"
  install -Dm755 verify.py "$pkgdir/usr/lib/facelock/verify.py"
  install -Dm755 pam_check.sh "$pkgdir/usr/lib/facelock/pam_check.sh"
  install -Dm644 facelock/*.py -t "$pkgdir/usr/lib/facelock/facelock/"
  install -Dm644 profiles/*.yaml -t "$pkgdir/usr/share/facelock/profiles/"
  install -Dm644 tuning/simple/*.yaml -t "$pkgdir/usr/share/facelock/tuning/simple/"
  install -Dm644 config.yaml "$pkgdir/usr/share/doc/facelock/config.example.yaml"
  install -Dm644 README.md "$pkgdir/usr/share/doc/facelock/README.md"
  install -Dm644 99-facelock-ir-led.rules \
    "$pkgdir/usr/lib/udev/rules.d/99-facelock-ir-led.rules"
  install -Dm644 config.yaml "$pkgdir/etc/facelock/config.yaml"
  # Keep biometric storage private to root; do not embed a build-host video GID.
  install -dm700 "$pkgdir/var/lib/facelock"
}
