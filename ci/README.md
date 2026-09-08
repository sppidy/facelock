# Package Releases

Only a push to `main` whose before/after trees differ at root `PKGBUILD`
creates a stable release. The comparison covers the entire push, not just
its last commit. Net-reverted changes do not count. Other branch pushes,
scheduled runs and manual branch runs create unique nightly prereleases.
Tag pushes do not trigger this workflow; manual runs targeting tags are skipped.

Stable tags are `v<pkgver>-<pkgrel>`. Bump `pkgrel` (and update `.SRCINFO`)
when publishing another packaging revision. Existing tags always fail closed;
the publisher never replaces releases, tags or assets. An interrupted draft
is retained for inspection, not silently overwritten. The old `v0.1.0` tag
is not used or changed by this workflow.

Both packages use one SHA256-verified `git archive` of `GITHUB_SHA` on the
native `ubuntu-24.04-arm` runner in Debian trixie. The checkout is mounted
read-only; makepkg and dpkg run as an unprivileged builder in separate trees.
Debian's makepkg uses `--nodeps` because Arch runtime packages are not Debian
build dependencies. This project has no compiled payload: the Arch package
declares `any`, while the Debian package currently declares `arm64`.

Local source overrides are `FACELOCK_SOURCE_ARCHIVE` and required
`FACELOCK_SOURCE_SHA256`. CI also sets `FACELOCK_PACKAGE_VERSION` for nightlies.
Without these variables PKGBUILD fetches the versioned public release tag.

`python3 -B -m unittest discover -s tests -v` runs offline classification and
syntax tests plus NumPy/OpenCV authentication regressions. Install
`numpy opencv-python-headless PyYAML` before running them. CI inspects package
metadata, paths, ownership,
modes, syntax and matching runtime payloads without installing or activating PAM.
Packages do not include Git internals, raw captures, downloaded models or
enrolled biometric data. Model setup, enrollment and PAM activation remain
manual. Set `runtime.stack` in a trusted configuration for privileged runs.
`FACELOCK_STAGED` is only a developer diagnostic override.

Each release includes the source archive, commit/run provenance, resolved
container image digest, Debian build-tool inventory and `SHA256SUMS`. The
publisher has write permission; the build has only read permission. It downloads
all draft assets and compares their bytes before making the release public.
These are packaging checks, not hardware/PAM authentication certification or
a claim of bit-for-bit reproducibility across changing Debian repositories.
