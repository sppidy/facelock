# A14 CPU imaging backport

`libcamera-0.7.1-imaging.patch` applies to the existing X1P 0.7.1 source
snapshot with the disjoint-route allocator and CPU SoftISP. It is a backport
for that snapshot, not a replacement for the current upstream libcamera tree.
Build libcamera and the simple IPA together: the statistics shared-memory
layout changes. The Python binding and proxy worker must match this libcamera
ABI and the installed Python version.

The upstream work credited here is by Milan Zamazal, Kieran Bingham, and
Martin Neiva de Carvalho. Their changes retain the upstream LGPL-2.1-or-later
license of the affected libcamera files:

- [Black level before white balance](https://lists.libcamera.org/pipermail/libcamera-devel/2026-June/059398.html)
- [Clamp white-balance gains before the color matrix](https://lists.libcamera.org/pipermail/libcamera-devel/2026-June/059399.html)
- [OV02C10 gain helper](https://patchwork.libcamera.org/patch/28174/)
- [Exclude clipped pixels from white balance](https://patchwork.libcamera.org/patch/28175/)
- [Estimate white balance from neutral pixels](https://patchwork.libcamera.org/patch/28176/)
- [Use the sensor's actual gain step](https://patchwork.libcamera.org/patch/28177/)

Backport adaptations retain the old `simple` IPA paths and float vector API,
use `max().min()` instead of the newer vector `clamp()`, and use the existing
AWB algorithm rather than importing the newer libipa AWB framework. A small
local addition makes `Agc.target` configurable (default 2.5, accepted range
1.1–4.0). The A14 tuning uses 1.8 to reduce gain and clipping in backlit scenes.
Only the CPU backend was built and tested; GPU use requires the complete
upstream black-level/saturation series and separate validation.

Apply to an isolated copy of the source snapshot:

```sh
patch --dry-run -p1 < /path/to/libcamera-0.7.1-imaging.patch
patch -p1 < /path/to/libcamera-0.7.1-imaging.patch
```

For an existing matching build, rebuild both targets:

```sh
ninja -C ../build src/libcamera/libcamera.so.0.7.1 src/ipa/simple/ipa_soft_simple.so
```

The tested Meson options include `pipelines=simple`, `ipas=simple`,
`pycamera=enabled`, `softisp-gpu=disabled`, `buildtype=release`, and
`cpp_args=-Wno-error=array-bounds` for the existing GCC 16 build warning.
Use `tools/stage_camera_runtime.py` to snapshot the resulting runtime,
including `build/src/libcamera/proxy/worker/soft_ipa_proxy`. Omitting that
executable can make a relocated runtime silently disable debayering and
negotiate raw Bayer even for a viewfinder request.
