# face-unlock — libcamera/ISP face authentication for Linux (PAM)

Howdy-style login, rebuilt for machines where the camera is **not** a UVC
webcam: Qualcomm CAMSS / libcamera `simple`-pipeline sensors (here:
`ov02c10` RGB + `hm1092` IR with `ir:flash` LED on a Snapdragon X laptop).

## How it differs from Howdy

| | Howdy | face-unlock |
|---|---|---|
| Camera API | OpenCV `VideoCapture` on `/dev/videoN` (UVC) | libcamera via GStreamer `libcamerasrc`, per-sensor select |
| Face engine | dlib (heavy, x86/CUDA baggage, dead upstream) | OpenCV YuNet + SFace ONNX on CPU (`cv2.dnn`) |
| Power | holds device open | opens camera per attempt, closes it — sensor suspends |
| IR | emitter hacks per driver | `ir:flash` LED + IR sensor as second factor path |

## Security scope (read this)

v1 is **convenience login**, not spoof-proof auth: no depth/liveness
check yet (IR presence match only). PAM entries are `sufficient`, so the
password path always still works. Embeddings live `0640 root:video` in
`/var/lib/facelock/` — group-readable (not root-only) because sudo runs PAM
auth helpers as the invoking user, not as root.

## Layout

- `facelock/capture.py` — on-demand raw-frame snapshot via gst-launch
- `facelock/recognize.py` — YuNet detect + SFace embed/cosine match
- `facelock/store.py` — embedding save/load
- `enroll.py` / `verify.py` — CLIs (`verify.py` is what PAM calls)
- `pam_check.sh` — `pam_exec` entry (NEVER denies, only succeeds/defers)
- `setup_models.py` — downloads YuNet + SFace ONNX models
- `config.yaml` — sensors, thresholds, LED (installed to `/etc/facelock/`)
- `install.sh` / `uninstall.sh` — system wiring (run on the machine)
- `detect_cameras.sh` — lists libcamera names to put in `config.yaml`
- `99-facelock-ir-led.rules` — lets `video` group drive the IR flash LED

## Bring-up

On the machine (needs sudo for pacman + PAM + udev):

```bash
cd ~/Projects/face-unlock
./detect_cameras.sh        # confirm/paste IR camera-name into config.yaml
./install.sh               # deps, models, udev, files, PAM (backs up first)
sudo python /usr/local/lib/facelock/enroll.py --sensor both
python /usr/local/lib/facelock/verify.py   # test as yourself first
```

Then test login/sudo/hyprlock from a **second session** before logging out.
If anything misbehaves: `./uninstall.sh` restores PAM backups.

## Porting to another ARM laptop

1. Run `facelock-detect` — it prints cameras, GStreamer elements, UVC
   nodes, LED flash nodes, and the pycamera API style.
2. Copy `profiles/_template.yaml` to `profiles/<machine>.yaml` and fill in
   the suggested values. `profiles/generic-uvc.yaml` covers plain webcams
   with no IR; `profiles/zenbook-a14.yaml` is the dual-sensor reference.
3. Install with your profile (backs up any existing config):
   `./install.sh --profile <machine>`
4. Enroll + verify, tune `match.*_threshold` to your lighting.
5. Send the profile upstream so the next machine works out of the box.

Notes for porters:

- UVC-only machines need just `cameras.rgb: uvc:/dev/videoN`
  (`capture.mode: uvc`) plus `gst-plugins-good` for `v4l2src`.
- Single-IR-sensor libcamera machines use `capture.mode: sequential`
  (RGB via `libcamerasrc`, IR via `cam` raw) — no kernel work needed.
- Concurrent dual (`dual-pycamera`) needs either luck with the stock
  route allocator or a board-specific mapping fix like the X1P one.
- `dual_capture.py` speaks both pycamera API styles (snake_case 0.7.1
  and camelCase); anything else falls back to sequential automatically.
- Packaged installs (`PKGBUILD`, Arch `any`) keep the same layout under
  `/usr` instead of `/usr/local`; PAM is wired explicitly with
  `facelock-pam-enable`, never automatically.
