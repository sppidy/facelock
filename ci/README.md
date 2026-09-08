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

Both packages use the same SHA256-verified Facelock source archive. The
embedded camera runtime is compiled separately on native ARM64: Arch Linux
ARM builds the pacman package, Debian trixie builds the `.deb`. Runtime bytes
may differ because the Python ABIs and system libraries differ; common
Facelock application files must match byte for byte. Both packages contain
ELF binaries and are architecture-specific (`aarch64` / `arm64`).

The Arch builder starts from the official rootfs, verified against the pinned
Arch Linux ARM signing fingerprint before import. Its rootfs hash and installed
package inventory are published. The Debian builder image digest and package
inventory are also published. Source is read-only in both containers; package
builds run as an unprivileged builder. The pinned libcamera commit is fetched
and patched without Meson dependency downloads. Native smoke tests check the
binding import and dynamic-library resolution before packaging.

Local source overrides are `FACELOCK_SOURCE_ARCHIVE` and required
`FACELOCK_SOURCE_SHA256`. CI also sets `FACELOCK_PACKAGE_VERSION` for nightlies.
Without these variables PKGBUILD fetches the versioned public release tag.

`python3 -B -m unittest discover -s tests -v` runs offline classification and
syntax tests plus NumPy/OpenCV authentication regressions. Install
`numpy opencv-python-headless PyYAML` before running them. CI inspects package
metadata, paths, ownership,
modes, syntax and matching runtime payloads without installing or activating PAM.
Packages include a private camera runtime and its licenses, but no Git internals, raw captures, downloaded models or
enrolled biometric data. Model setup, enrollment and PAM activation remain
manual. Set `runtime.stack` in a trusted configuration for privileged runs.
`FACELOCK_STAGED` is only a developer diagnostic override.

Each release includes the source archive, commit/run provenance, resolved
container image digest, Debian build-tool inventory and `SHA256SUMS`. The
publisher has write permission; the build has only read permission. It downloads
all draft assets and compares their bytes before making the release public.
These are packaging checks, not hardware/PAM authentication certification or
a claim of bit-for-bit reproducibility across changing Debian repositories.
