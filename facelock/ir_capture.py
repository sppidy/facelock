"""IR capture via `cam` raw frames (hm1092 mono sensor).

GStreamer/libcamerasrc cannot negotiate this sensor's R10_CSI2P stream, but
libcamera itself streams it fine — so we capture raw .bin with `cam` and
unpack MIPI CSI-2 10-bit packing in numpy. Sensor still opened per call.
Layout: 560x360, 704-byte stride (700 packed + 4 pad)."""
import glob
import os
import shutil
import subprocess
import tempfile
import time

import cv2
import numpy as np

W, H, STRIDE = 560, 360, 704


def unpack_r10(path):
    a = np.frombuffer(open(path, "rb").read(), np.uint8).reshape(H, STRIDE)
    d = a[:, :700].reshape(H, 140, 5)
    hi = d[..., :4].astype(np.uint16)
    lo = d[..., 4].astype(np.uint16)
    img = np.empty((H, W), np.uint16)
    img[:, 0::4] = (hi[..., 0] << 2) | (lo & 0x3)
    img[:, 1::4] = (hi[..., 1] << 2) | ((lo >> 2) & 0x3)
    img[:, 2::4] = (hi[..., 2] << 2) | ((lo >> 4) & 0x3)
    img[:, 3::4] = (hi[..., 3] << 2) | ((lo >> 6) & 0x3)
    return (img >> 2).astype(np.uint8)  # 10-bit -> 8-bit gray


def set_led(path, value):
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except OSError:
        return False


def fire_strobe(strobe_path, brightness_path=None, brightness=0):
    """Fire the flash-class strobe (real illuminator). Falls back to torch."""
    if strobe_path:
        set_led(strobe_path, 0)  # re-arm: a latched 1 won't refire
        time.sleep(0.05)
        if set_led(strobe_path, 1):
            return "strobe"
    if brightness_path and set_led(brightness_path, brightness):
        return "torch"
    return "none"


def snapshot_ir(camera, led_path=None, led_brightness=128, frames=10,
                timeout=30, strobe_path=None):
    """Returns BGR-stacked IR frame (for YuNet/SFace) or raises."""
    tmpd = tempfile.mkdtemp(prefix="facelock_ir_")
    mode = fire_strobe(strobe_path, led_path, led_brightness)
    try:
        # NOTE: cam keeps a literal '=' in '-F=...' values; attached form only.
        cmd = ["cam", "-c", camera, f"--capture={frames}", f"-F{tmpd}/"]
        subprocess.run(cmd, timeout=timeout, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        files = sorted(glob.glob(os.path.join(tmpd, "*.bin")))
        if not files:
            raise RuntimeError("cam captured no frames")
        gray = unpack_r10(files[-1])
    finally:
        if mode == "torch" and led_path:
            set_led(led_path, 0)
        # strobe auto-expires after flash_timeout; nothing to switch off
        shutil.rmtree(tmpd, ignore_errors=True)
    if gray.mean() < 2.0:
        raise RuntimeError("IR frame black (LED off? sensor covered?)")
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
