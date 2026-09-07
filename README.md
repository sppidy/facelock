# face-unlock — libcamera/ISP face authentication for Linux (PAM)

Howdy-style login, rebuilt for machines where the camera is **not** a UVC
webcam: Qualcomm CAMSS / libcamera `simple`-pipeline sensors (here:
`ov02c10` RGB + `hm1092` IR with `ir:flash` LED on a Snapdragon X laptop).

## Requirements (read before installing)

**This is not a drop-in replacement for stock libcamera.** Concurrent dual
capture needs two things that upstream libcamera does not provide on
Qualcomm CAMSS:

1. **A patched libcamera** with the disjoint-routes allocator
   (`prefer_disjoint_routes`), so both sensors get separate CSID/VFE paths
   instead of fighting over `msm_csid0`. The patch lives in the
   `a14-scratch` research tree and is applied to a staged build (see
   `research/x1p-libcamera-disjoint-routes.patch` and
   `zenbook-staging-20260906.md` for the exact tree/config).
2. **A patched kernel** with the correct full/lite CSID and VFE mapping for
   X1P (`research/x1p-camss-normal-world-mapping.patch`). Stock kernels
   route both sensors onto `msm_csid0` and the second `start()` fails with
   `-EBUSY`.

On the Zenbook A14 the patched stack lives at
`~/scratch/x1p-concurrency-20260906/` (kernel in
`/lib/modules/*/updates/qcom-camss.ko`, libcamera under
`build/src/libcamera`); `facelock-run` wires that staged tree into
`LD_LIBRARY_PATH`/`PYTHONPATH`/`XDG_CONFIG_HOME` and falls back to the
legacy sequential paths automatically when it's absent.

**Without those two patches you get RGB-only auth.** IR falls back to the
sequential `cam` raw path (slower, single-sensor, still works), but the
concurrent RGB+IR path that makes v1 feel like Windows Hello is not
available on stock libcamera.

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
