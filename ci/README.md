# Package Releases

Pushes to `main` create nightly prereleases. Scheduled runs do the same, except
when that commit already has a nightly tag. Pull requests run offline tests only.
Stable releases require an explicit `workflow_dispatch` run with `channel=stable`;
the default manual channel is nightly. Bump `pkgrel` and update `.SRCINFO` before
requesting another stable release.

Stable builds produce `facelock`; nightly builds produce `facelock-nightly`.
The package identities conflict because they install the same commands and
configuration. GitHub marks nightlies as prereleases and stable builds as normal
releases; only a stable release may become the repository's latest release.

Stable tags are `v<pkgver>-<pkgrel>`. Existing tags always fail closed;
the publisher never replaces releases, tags or assets. An interrupted draft
is retained for inspection, not silently overwritten. The old `v0.1.0` tag
is not used or changed by this workflow.

Both packages use the same SHA256-verified Facelock source archive. The
embedded camera runtime is compiled separately on native ARM64: Arch Linux
ARM builds the pacman package, Debian trixie builds the `.deb`. Runtime bytes
may differ because the Python ABIs and system libraries differ; common
Facelock application files must match byte for byte. Both packages contain
ELF binaries and are architecture-specific (`aarch64` / `arm64`).

The Arch builder starts from an official rootfs mirror and requires both its pinned
SHA256 and Arch Linux ARM signature before import. Its rootfs hash and installed
package inventory are retained in the CI artifact. The Debian builder image digest
and dated Debian snapshot are pinned; its package inventory is retained there too.
Source is read-only in both containers; package
builds run as an unprivileged builder. The pinned libcamera commit is fetched
and patched without Meson dependency downloads. Native smoke tests check the
binding import and dynamic-library resolution before packaging.

Local source overrides are `FACELOCK_SOURCE_ARCHIVE` and required
`FACELOCK_SOURCE_SHA256`. CI also sets `FACELOCK_PACKAGE_VERSION` and
`FACELOCK_PACKAGE_NAME` for nightlies.
Without these variables PKGBUILD fetches the versioned public release tag.

`python3 -B -m unittest discover -s tests -v` runs offline classification and
syntax tests plus NumPy/OpenCV authentication regressions. Install
`numpy opencv-python-headless PyYAML` before running them. CI inspects package
metadata, paths, ownership,
modes, syntax and matching runtime payloads. It also installs and removes each
package inside its disposable native container and verifies that PAM files do not change.
Packages include a private camera runtime and its licenses, but no Git internals, raw captures, downloaded models or
enrolled biometric data. Model setup, enrollment and PAM activation remain
manual. Set `runtime.stack` in a trusted configuration for privileged runs.
`FACELOCK_STAGED` is only a developer diagnostic override.

Each release exposes only the Debian package, Arch package, corresponding patched
libcamera source and `SHA256SUMS`. GitHub adds its standard source-code archives.
Build inventories, repository databases and provenance stay in the CI artifact or
the pacman repository site. Release notes list the actual commits since the
previous release of the same channel. The publisher has write permission; the
build has only read permission. It downloads all draft release assets and compares
their bytes before making the release public.
These are packaging checks, not hardware/PAM authentication certification or
a claim of bit-for-bit reproducibility across different build systems.

The Pages site contains independent `[facelock-stable]` and `[facelock-nightly]`
pacman repositories under `/stable/$arch` and `/nightly/$arch`. Each contains the
newest package for that channel. Nightly publication retains the five newest
nightly prereleases and removes older nightly releases and tags.
