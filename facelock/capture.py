"""On-demand libcamera snapshots. Opens the sensor per call, closes it on
return — never a persistent stream, so the node runtime-suspends after."""
import os
import subprocess
import time

import numpy as np


def snapshot(camera, width=640, height=480, warmup_sec=1.5,
             path="/tmp/facelock_frame.raw", timeout=30):
    """Capture one BGR frame (last frame after warm-up). Returns ndarray.

    camera is either a libcamera camera-name, or 'uvc:/dev/videoN' for
    plain USB webcams (uses v4l2src, no libcamera needed).
    """
    frame_bytes = width * height * 3
    for p in (path, path + ".log"):
        try:
            os.remove(p)
        except OSError:
            pass
    if camera.startswith("uvc:"):
        src = ["v4l2src", f"device={camera[4:]}"]
    else:
        src = ["libcamerasrc", f"camera-name={camera}"]
    cmd = ["gst-launch-1.0", "-q"] + src + [
        "!", "videoconvert", "!", "videoscale", "!",
        f"video/x-raw,format=BGR,width={width},height={height}",
        "!", "filesink", f"location={path}",
    ]
    log = open(path + ".log", "wb")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=log)
    try:
        time.sleep(warmup_sec)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()
    try:
        with open(path, "rb") as f:
            blob = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    n = len(blob) // frame_bytes
    if n < 1:
        tail = ""
        try:
            with open(path + ".log", "rb") as f:
                tail = f.read().decode("utf-8", "replace")[-800:]
        except OSError:
            pass
        try:
            os.remove(path + ".log")
        except OSError:
            pass
        raise RuntimeError(f"no frames captured ({len(blob)} bytes). "
                           f"gst log: {tail}")
    try:
        os.remove(path + ".log")
    except OSError:
        pass
    return np.frombuffer(blob[(n - 1) * frame_bytes:n * frame_bytes],
                         dtype=np.uint8).reshape(height, width, 3).copy()


def led(path, value):
    """Set IR flash LED. Returns True on success (needs udev rule for user)."""
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except OSError:
        return False
