#!/usr/bin/env python3
"""Single-process dual-camera test: can one CameraManager stream the RGB
(ov02c10) and IR (hm1092) sensors at the same time? Needs python-libcamera.
Run:  sudo pacman -S --needed python-libcamera && python dual_test.py
"""
import select
import sys
import time
import traceback

from libcamera import CameraManager, FrameBufferAllocator, StreamRole

RGB_ID = "camera@36"   # ov02c10
IR_ID = "camera@24"    # hm1092
NREQ = 4


def open_cam(cm, match, role):
    cam = next(c for c in cm.cameras if match in c.id)
    cam.acquire()
    try:
        cfg = cam.generate_configuration([role])
        sc = cfg.at(0)
        print(f"[{match}] default: "
              f"{sc.size.width}x{sc.size.height}", flush=True)
        ret = cam.configure(cfg)
        if ret is not None and ret < 0:
            raise RuntimeError(f"configure failed: {ret}")
        alloc = FrameBufferAllocator(cam)
        n = alloc.allocate(sc.stream)
        if n is not None and n <= 0:
            raise RuntimeError(f"allocate failed: {n}")
        return cam, sc.stream, alloc
    except Exception:
        cam.release()
        raise


def queue_some(cam, stream, alloc, cookie, n):
    for buf in alloc.buffers(stream)[:n]:
        req = cam.create_request(cookie)
        req.add_buffer(stream, buf)
        cam.queue_request(req)


def main():
    cm = CameraManager.singleton()
    print("cameras:", [c.id for c in cm.cameras], flush=True)
    rgb = ir = None
    try:
        rgb, rgb_stream, rgb_alloc = open_cam(cm, RGB_ID,
                                              StreamRole.Viewfinder)
        print("[rgb] acquired+configured", flush=True)
        ir, ir_stream, ir_alloc = open_cam(cm, IR_ID, StreamRole.Raw)
        print("[ir] acquired+configured", flush=True)
        rgb.start()
        print("[rgb] started", flush=True)
        queue_some(rgb, rgb_stream, rgb_alloc, 1, NREQ)
        ir.start()
        print("[ir] started", flush=True)
        queue_some(ir, ir_stream, ir_alloc, 2, NREQ)
        got = {1: 0, 2: 0}
        deadline = time.time() + 15
        while time.time() < deadline and \
                (got[1] < NREQ or got[2] < NREQ):
            select.select([cm.event_fd], [], [], 1.0)
            for req in cm.get_ready_requests():
                if req.status == req.Status.Complete:
                    got[req.cookie] = got.get(req.cookie, 0) + 1
        ok = got[1] >= NREQ and got[2] >= NREQ
        print(f"DUAL {'OK' if ok else 'TIMEOUT'}: "
              f"rgb={got[1]} ir={got[2]}", flush=True)
        return 0 if ok else 1
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        for cam in (rgb, ir):
            if cam is not None:
                try:
                    cam.stop()
                except Exception:
                    pass
                cam.release()


if __name__ == "__main__":
    sys.exit(main())
