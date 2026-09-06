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
password path always still works. Embeddings live root-owned `0600` in
`/var/lib/facelock/`.

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
