# Maintainer: sppidy
pkgname=facelock
pkgver=0.1.0
pkgrel=1
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
makedepends=('git')
backup=('etc/facelock/config.yaml')
source=("git+https://github.com/sppidy/facelock.git#tag=v${pkgver}")
sha256sums=('SKIP')
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
  install -Dm600 /dev/null "$pkgdir/etc/facelock/config.yaml"
  cp config.yaml "$pkgdir/etc/facelock/config.yaml"
  chmod 644 "$pkgdir/etc/facelock/config.yaml"
  install -dm750 -g video "$pkgdir/var/lib/facelock"
}
