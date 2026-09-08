# Facelock

Face login for Linux through PAM, using libcamera or a USB webcam and
OpenCV's YuNet and SFace models. The main target is the ASUS Zenbook A14
with OV02C10 RGB and HM1092 IR cameras.

If capture or matching fails, PAM continues to your password. The IR check
measures the face region's response to the illuminator, but prints and
screens can reflect IR too. Facelock has not been validated against spoofing.

## Camera support

| Profile | Required cameras | Notes |
| --- | --- | --- |
| `zenbook-a14` | RGB and IR | Both must match, even in low light. Requires a patched camera stack. |
| `zenbook-a14-ir` | IR | For use in the dark. Requires fresh enrollment. |
| `generic-uvc` | RGB | Set the USB webcam device path before enrolling. |

Use `profiles/_template.yaml` for another machine. `facelock-detect` lists
cameras and LED controls to help fill it in. Cameras that cannot run together
can use `capture.mode: sequential`. Facelock never switches to fewer cameras
when one fails.

## Setup

Packages install under `/usr` and leave PAM disabled. After installing a
package, copy a profile and edit it for your account and cameras:

```sh
sudo cp /usr/share/facelock/profiles/zenbook-a14.yaml /etc/facelock/config.yaml
sudoedit /etc/facelock/config.yaml
```

Set `pam.user` to your username. A14 users also need to configure the
[camera runtime](#a14-camera-runtime) below before enrolling.

Download the models, enroll, then check that a new capture matches.
Replace `YOUR_USER` with your username:

```sh
sudo python3 /usr/lib/facelock/setup_models.py
sudo facelock-run /usr/lib/facelock/enroll.py --user YOUR_USER
sudo facelock-run /usr/lib/facelock/verify.py --user YOUR_USER --json
```

Once verification works, enable face login for the PAM services you use:

```sh
sudo facelock-pam-enable sudo login
```

The helper backs up each service before editing it. Keep a second session
open while testing. Enrollment and verification need root access; lock
screens that run PAM without privileges will continue to use passwords.

For a development install, run `./install.sh --profile=zenbook-a14 --no-pam`.
It installs the application under `/usr/local`; adjust the commands above
accordingly. Add `--pam` only when you want the installer to enable PAM.

## A14 camera runtime

The A14 needs additional kernel and libcamera patches for concurrent RGB/IR
capture. The patches and application instructions are included in
[`patches/`](patches/README.md).

The [imaging backport](patches/README.md#cpu-imaging-backport) provides the CPU pipeline
fixes needed by the included OV02C10 tuning. The A14 profile captures the full
1920×1080 view and resizes it to 640×360; requesting a smaller image directly
from this SoftISP build crops the view instead.

Copy a matching build into a fresh, root-owned directory:

```sh
sudo python3 tools/stage_camera_runtime.py \
  /path/to/camera-build /opt/facelock/camera-stack
```

Set `runtime.stack: /opt/facelock/camera-stack` in the configuration. The tool
copies the libraries, IPA, proxy worker and Python binding. The binding must
match your system Python version. Root authentication rejects runtime paths
that an unprivileged user can modify, including builds in a home directory.

The A14 profiles already set `runtime.tuning: /usr/share/facelock/tuning`.
They use the flash illuminator; select torch mode only on hardware where it
works.

## Upgrading from 0.1

Enroll again after upgrading. Version 0.2 does not accept the old `.npz`
records, which came from a group-writable store. New `.face` records are
bound to the account and capture settings, with an HMAC to detect changes.
They are not encrypted. The store is root-owned with mode `0700`, and records
and the signing key use `0600`.

Package upgrades preserve your configuration. Compare it with the new example,
then verify and rerun `facelock-pam-enable` to update the PAM entry. Changing
required cameras or capture settings also requires fresh enrollment.

## Troubleshooting

Check capture without loading the face models:

```sh
sudo facelock-run /usr/lib/facelock/diagnose.py --camera-only
```

Omit `--camera-only` to include face detection and image quality checks.
Add `--save-frames /private/directory` to save the last images. Diagnostics
leave enrollment untouched; normal authentication saves no images.
Verification details are logged to `/var/lib/facelock/attempts.jsonl` by default.
Close other camera applications before testing.

For unprivileged diagnostics with a development build:

```sh
FACELOCK_STAGED=/path/to/camera-build \
  facelock-run /usr/lib/facelock/diagnose.py --config /path/to/test.yaml
```

Root runs ignore `FACELOCK_STAGED` and use `runtime.stack` instead.

## Tests

```sh
python3 -m venv .venv
.venv/bin/pip install numpy opencv-python-headless PyYAML
.venv/bin/python -B -m unittest discover -s tests -v
```

The tests cover capture timing, pixel conversion, matching policy, enrollment
integrity, runtime isolation and packaging. They run without camera hardware.
See [ci/README.md](ci/README.md) for package builds and releases.
