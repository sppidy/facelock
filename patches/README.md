# A14 camera patches

These patches provide concurrent RGB/IR capture and CPU image processing
for the Zenbook A14. Apply the CAMSS patch to the kernel source and reboot. The package build applies the other two patches to its private
libcamera copy; no system libcamera reinstall is needed.

| Patch | Target | Purpose |
| --- | --- | --- |
| [CAMSS mapping](x1p-camss-normal-world-mapping.patch) | Linux | Align X1P camera resources and links. |
| [Camera routing](x1p-libcamera-disjoint-routes.patch) | libcamera | Let the cameras use separate capture routes. |
| [CPU imaging](libcamera-0.7.1-imaging.patch) | libcamera 0.7.1 | Correct black level, gain and white balance. |

## Routing

The CAMSS patch records its kernel base commit in the header. The libcamera
patches target upstream commit `597a5bb97bf9257790edf21020c679aa666ba307`
(0.7.1 plus upstream fixes), not the pristine v0.7.1 tag. The automated build
pins this commit and refuses patch failures. From each target source tree, check the relevant patch
before applying it:

```sh
patch --dry-run -p1 < /path/to/facelock/patches/PATCH_NAME
patch -p1 < /path/to/facelock/patches/PATCH_NAME
```

Apply the routing patch before the imaging backport. Enable separate routes
in the camera runtime's `opt-in/libcamera/configuration.yaml`:

```yaml
pipelines:
  simple:
    prefer_disjoint_routes: true
```

The launcher selects this configuration when `runtime.stack` points to the
camera runtime. See [A14 camera runtime](../README.md#a14-camera-runtime)
for the staging command.

## CPU imaging backport

`libcamera-0.7.1-imaging.patch` targets the pinned commit above with the routing patch
and CPU SoftISP. It needs adaptation for newer libcamera versions.
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
patch --dry-run -p1 < /path/to/facelock/patches/libcamera-0.7.1-imaging.patch
patch -p1 < /path/to/facelock/patches/libcamera-0.7.1-imaging.patch
```

For an existing matching build, rebuild both targets:

```sh
ninja -C ../build src/libcamera/libcamera.so.0.7.1 src/ipa/simple/ipa_soft_simple.so
```

The tested Meson options include `pipelines=simple`, `ipas=simple`,
`pycamera=enabled`, `softisp-gpu=disabled`, `buildtype=release`, and
`cpp_args=-Wno-error=array-bounds` for the existing GCC 16 build warning.
Use [stage_camera_runtime.py](../tools/stage_camera_runtime.py) to copy the resulting runtime,
including `build/src/libcamera/proxy/worker/soft_ipa_proxy`. Omitting that
executable can make a relocated runtime silently disable debayering and
negotiate raw Bayer even for a viewfinder request.

## Mandatory running-kernel capability

The CAMSS patch includes a read-only device attribute. After booting the
patched kernel, check:

```sh
cat /sys/bus/platform/drivers/qcom-camss/*/facelock_capability
```

The X1P device must report `x1p-normal-world-v1`. Facelock does not accept a
configuration-only declaration or a kernel-name match. If you previously
applied the mapping fix, port the final `facelock_capability` hunk to that
kernel and rebuild; do not reapply the mapping hunks blindly. Other SoCs
report `unsupported`. USB profiles do not require this A14-specific patch.
