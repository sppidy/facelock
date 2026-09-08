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

Enrollment collects three consistent samples from separate bursts. Each burst
shows detection score, blur and brightness. Poor captures are retried, up to
five bursts by default. You review the result and confirm before anything is
saved; declining keeps any existing enrollment. Use `--yes` for an explicit
noninteractive save. `enrollment.max_bursts` and `enrollment.timeout_sec`
control the retry limit and overall deadline.

For a desktop wizard, run `facelock-enroll` as your normal user, or open
**Facelock Enrollment** from the application menu. It uses the same capture
checks and has a separate **Save enrollment** button. The system authorization
prompt grants the helper access to the cameras and enrollment store.
Install `gtk4`, `python-gobject` and `polkit` on Arch, or `python3-gi`,
`gir1.2-gtk-4.0` and `pkexec` on Debian. Your desktop needs a Polkit
authentication agent. The terminal wizard does not need these GUI packages.

Once verification works, enable face login for the PAM services you use:

```sh
sudo facelock-pam-enable sudo login
```

The helper backs up each service before editing it. Keep a second session
open while testing. Enrollment and direct verification need root access.
Unprivileged lock screens use the optional authentication socket below.

For a development install, run `./install.sh --profile=zenbook-a14 --no-pam`.
It installs the application under `/usr/local`; adjust the commands above
accordingly. Add `--pam` only when you want the installer to enable PAM.

## A14 camera runtime

The A14 requires the [CAMSS kernel patch](patches/README.md). Install a kernel
with that patch and reboot before using Facelock. The patch now exposes a
read-only `facelock_capability` attribute on the CAMSS device. A14 profiles
require `x1p-normal-world-v1`; missing capability stops the attempt before
camera access. Older kernels carrying the mapping fix alone need the marker
addition too. A kernel version string is not accepted as proof.

The `.deb` and Arch packages embed a private patched libcamera runtime at
`/usr/lib/facelock/camera`. The A14 profiles select it automatically. Libraries,
Python bindings, the simple IPA, proxy worker, routing configuration and tuning
are built and shipped together. System libcamera is not patched or replaced,
and no global loader configuration or GStreamer plugin is installed.

Each distribution builds its own runtime against its Python and system
libraries. Arch packages are now `aarch64`, Debian packages `arm64`. A Python
ABI change requires rebuilding/reinstalling the matching Facelock package;
the launcher rejects a mismatched binding. The runtime is dependency-isolated,
not a container security sandbox; it still uses the host kernel and devices.

The build pins upstream commit
`597a5bb97bf9257790edf21020c679aa666ba307` (0.7.1 plus upstream fixes), applies
the two bundled libcamera patches, removes build-directory RPATHs, and verifies
that the Python binding loads with private libcamera libraries. A manifest
records source, patch hashes, build options, Python ABI and architecture.
Releases include the corresponding patched libcamera source and license files.

An existing `/etc/facelock/config.yaml` is preserved on upgrade. To migrate
from a manually staged runtime, set:

```yaml
runtime:
  require_camss: true
  stack: /usr/lib/facelock/camera
  tuning: /usr/lib/facelock/camera/tuning
```

Then run diagnostics and explicitly enroll again, because changing the camera
runtime changes the enrollment signature. Keep the previous package and config
for rollback. Development snapshots through `runtime.stack` remain supported;
root authentication requires trusted root-owned paths throughout.

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

## Unlock feedback

For hyprlock and other unprivileged PAM callers, enable the system
authentication socket after installing and verifying enrollment:

```sh
sudo systemctl enable --now facelock-auth.socket
```

The existing `pam_check.sh` automatically uses this socket for unprivileged
callers. Root callers such as greetd keep using the direct verifier. The
broker derives the account from the connecting process's kernel credentials;
clients cannot choose another account, a command, configuration, or enrollment
write. Missing services and verification failures continue to the password
stack. Each request is isolated in a root systemd service with a 45-second
limit, and the existing capture lock prevents concurrent camera attempts.

Configure the PAM service used by the lock screen with
`sudo facelock-pam-enable hyprlock`. If `hyprlock` already includes a `login`
stack containing Facelock, that inherited entry is sufficient; adding another
entry would attempt face verification twice on failure. Hyprlock uses the
`hyprlock` PAM service by default. Its normal authentication action (usually
Enter) starts verification; displaying a status label does not start a scan.
To disable the unprivileged broker, run
`sudo systemctl disable --now facelock-auth.socket`.

Enable the status service in your user session:

```sh
systemctl --user enable --now facelock-feedback.service
facelock-feedback --watch
```

It streams JSON lines with `state` (`scanning`, `matched`, `no-face`,
`no-match`, `unavailable` or `idle`) and `reenroll_suggested`. Without
`--watch`, the command reads the current status once. `--text` prints a label.
For a [hyprlock label](https://wiki.hypr.land/hypr-ecosystem/user/hyprlock/):

```ini
label {
    text = cmd[update:500] facelock-feedback --text
}
```

Wrappers can also connect to `/run/user/UID/facelock/feedback.sock` and send
`{"op":"watch"}` followed by a newline, or `{"op":"get"}` for one update.
The socket is private to that user. Only root publishers can supply status;
messages contain no images, embeddings, scores or account names. A missing
service does not block authentication. Stale status expires automatically.

A greetd wrapper running under a separate greeter account needs that account's
feedback service running before login. Set `feedback.greeter_user` to its
account name in `/etc/facelock/config.yaml`; PAM attempts from `greetd` then
send status there too. The greeter receives no re-enrollment suggestions.

Feedback is for display only. A `matched` event must never dismiss a lock
screen or substitute for PAM success. This service does not give an
unprivileged PAM caller access to the private enrollment store. Only the
separate root-owned `/run/facelock-auth/auth.sock` supplies a verification
result to the PAM helper; display feedback cannot supply that result.

## Re-enrollment suggestions

Three consecutive successful matches below 0.55 trigger a suggestion to
enroll again. A successful match at or above 0.55 resets the count; failed
attempts do not count. With multiple cameras, the lowest score among the
required cameras is used, taking each camera's second-best matching frame.

The prompt appears in verification output and unlock feedback. It stays
pending until you save a new enrollment. Facelock never updates your face
record or lowers a matching threshold in response to drift.

## Experimental A14 depth liveness

The built-in RGB/IR pair can now use calibrated stereo reconstruction as an
additional gate. It requires concurrent `dual-pycamera` capture with both
sensors. It is disabled by default and is not a validated spoof detector.
RGB/IR appearance differences and the short camera baseline may leave too
little reliable depth for unlocking. Hardware performance has not been
validated; masks and curved presentations are outside this geometric check.

First keep `depth.enabled: false` and select the A14 dual-camera profile.
Use a rigid checkerboard whose pattern is clearly visible in both RGB and IR.
Measure the square edge length. Hold the board still during each capture;
collect at least 15 usable pairs with varied board positions, distances and
tilts, including the area where your face will be. Repeat this command for
each pose (it creates a new private subdirectory each time):

```sh
sudo facelock-run /usr/lib/facelock/diagnose.py --camera-only \
  --save-stereo-pair /var/lib/facelock/stereo-calibration
```

Fit the calibration using the board's **inner corner** counts and measured
square size in metres. For a 9-by-6 board with 20 mm squares:

```sh
sudo python3 /usr/lib/facelock/calibrate.py \
  /var/lib/facelock/stereo-calibration /etc/facelock/stereo.json \
  --columns 9 --rows 6 --square-m 0.020
```

The fitter rejects mixed capture settings, unsynchronized pairs, insufficient
board detections, and reprojection RMS above one pixel. Low RMS alone does not
establish reliable face-depth accuracy; retain captures for inspection.
The output is mode `0600` and never overwrites an existing calibration.
Set the following in `/etc/facelock/config.yaml`, then run diagnostics with
your face in view and explicitly enroll again:

```yaml
depth:
  enabled: true
  calibration: /etc/facelock/stereo.json
```

Only illuminated IR exposures paired with distinct RGB frames within
`depth.max_skew_ms` (40 ms by default) are used. Calibration rectifies both
images; bidirectional disparity consistency filters ambiguous correspondence.
Depth is projected back onto the exact RGB image used for recognition.
No holes are filled and no depth is inferred from a single image.

At least two matching, quality-passing RGB frames must also pass depth in each
burst, and the existing IR match and illumination checks must pass. The face
interior needs 85% valid depth within 0.2–1.5 m, relief of 12–120 mm after
removing a fitted plane, and a central protrusion of at least 8 mm relative to
both sides. These experimental defaults are configured by
`min_valid_fraction`, `min_distance_m`, `max_distance_m`, `min_relief_m`,
`max_relief_m`, and `min_protrusion_m` under `depth`.

Missing calibration, stale or unmatched exposures, poor correspondence and
flat depth fail the attempt; there is no fallback to authentication without
depth. The calibration contents and depth policy are bound to enrollment, so
changing either requires user-confirmed re-enrollment. Existing profiles with
depth disabled retain their enrollment signatures. The enrollment wizard and
diagnostics report depth rejection reasons; normal authentication stores no
depth maps or stereo images.

The geometry follows OpenCV's
[stereo calibration and reconstruction APIs](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).

## Tests

```sh
python3 -m venv .venv
.venv/bin/pip install numpy opencv-python-headless PyYAML
.venv/bin/python -B -m unittest discover -s tests -v
```

The tests cover capture timing, pixel conversion, matching policy, enrollment
integrity, runtime isolation and packaging. They run without camera hardware.
See [ci/README.md](ci/README.md) for package builds and releases.
