# Changelog

## [0.3.0]

### Added

- Three-sample, quality-gated enrollment with per-burst score, blur and brightness feedback.
- Lock-screen feedback states and a root-owned authentication broker for PAM clients.
- User-confirmed drift warnings after three accepted matches below 0.55 similarity.
- Experimental calibrated RGB/IR stereo depth liveness for the Zenbook A14.
- A private patched libcamera runtime embedded in the Debian and Arch packages.
- An optional frontal-pose and coarse eye-direction attention gate.

### Changed

- Zenbook A14 capture now requires the CAMSS `x1p-normal-world-v1` capability marker.
- Stable packages are named `facelock`; nightly packages are named `facelock-nightly`.
- Native packages are built independently against their distribution's Python ABI.

### Upgrade notes

- Install the updated CAMSS kernel patch and reboot before using an A14 camera profile.
- Existing system libcamera installations are left untouched.
- PAM activation and re-enrollment remain explicit user actions.

## [0.2.0]

### Added

- Concurrent RGB and IR capture, machine profiles and PAM integration.
