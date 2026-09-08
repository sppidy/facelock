# Embedded camera runtime

Both package recipes invoke `build.py`; it builds CPU SoftISP and Python
bindings from a pinned upstream libcamera commit plus the bundled routing
and imaging patches. Do not run `meson install` into the system prefix.

For an isolated development build after installing the dependencies listed in
`PKGBUILD` or `debian/control`:

```sh
python3 camera-runtime/build.py --work /tmp/facelock-camera-build \
  --output /tmp/facelock-camera-runtime --jobs 4
```

Both paths must be fresh. `--source /path/to/libcamera-git` optionally uses a
local mirror; the pinned commit is still mandatory. Build on the target
architecture and distribution. The installed Python ABI must match the one
recorded in `manifest.json`. The Arch recipe currently requires Python 3.14
and declares `<3.15`; update those bounds deliberately when rebuilding for a
new Python minor version. Debian derives its minor-version bounds at build time. There is no precompiled foreign-distribution
fallback, and Meson cannot download unpinned dependencies.

The package payload contains only the runtime under `/usr/lib/facelock/camera`.
Build-time RPATHs are removed; Facelock's sanitized worker environment selects
its private libraries and IPA. The build checks every ELF dependency with
`ldd` and imports the private Python binding without camera access. The
corresponding patched source archive is emitted beside the runtime and included
in release assets. Upstream license texts are installed under `camera/LICENSES`.

This is library isolation, not a security sandbox. A14 capture additionally
requires the live CAMSS capability described in `patches/README.md`. Neither
package installs a kernel or enables PAM automatically.
