#!/usr/bin/env python3
"""Build a pinned, private libcamera payload for the build host's Python ABI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from stage_camera_runtime import stage

COMMIT = '597a5bb97bf9257790edf21020c679aa666ba307'
URL = 'https://git.libcamera.org/libcamera/libcamera.git'
PREFIX = '/usr/lib/facelock/camera'
PATCHES = ['x1p-libcamera-disjoint-routes.patch', 'libcamera-0.7.1-imaging.patch',
           'libcamera-pybind11-v3-smart-holder.patch']


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def build(work, output, source=None, jobs=2):
    work, output = Path(work).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    work.mkdir(parents=True, exist_ok=False)
    checkout = work / 'source'
    run('git', 'clone', '--no-checkout', source or URL, str(checkout))
    run('git', '-C', str(checkout), 'checkout', '--detach', COMMIT)
    actual = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != COMMIT:
        raise ValueError('unexpected libcamera source commit')
    for name in PATCHES:
        run('git', 'apply', '--check', str(ROOT / 'patches' / name), cwd=checkout)
        run('git', 'apply', str(ROOT / 'patches' / name), cwd=checkout)
    options = ['--buildtype=release', '--wrap-mode=nofallback', '--prefix=' + PREFIX,
               '--libdir=lib', '--sysconfdir=' + PREFIX + '/etc',
               '-Dpipelines=simple', '-Dipas=simple', '-Dpycamera=enabled',
               '-Dsoftisp-gpu=disabled', '-Dcam=disabled', '-Dqcam=disabled',
               '-Ddocumentation=disabled', '-Dgstreamer=disabled', '-Dv4l2=disabled',
               '-Dlc-compliance=disabled', '-Dtest=false', '-Dtracing=disabled',
               '-Dlibdw=disabled', '-Dlibunwind=disabled', '-Dcpp_args=-Wno-error=array-bounds']
    run('meson', 'setup', str(work / 'build'), str(checkout), *options)
    run('ninja', '-C', str(work / 'build'), '-j', str(jobs))
    route = work / 'opt-in/libcamera/configuration.yaml'
    route.parent.mkdir(parents=True)
    route.write_text('version: 1\nconfiguration:\n  pipelines:\n    simple:\n'
                     '      prefer_disjoint_routes: true\n')
    stage(work, output)
    shutil.copytree(ROOT / 'tuning', output / 'tuning')
    shutil.copytree(checkout / 'LICENSES', output / 'LICENSES')
    # Build-tree RPATHs must never allow a privileged worker to load libraries
    # from the writable build directory after installation.
    for path in output.rglob('*'):
        if path.is_file() and path.read_bytes()[:4] == b'\x7fELF':
            run('patchelf', '--remove-rpath', str(path))
    manifest = {'schema': 1, 'upstream': URL, 'commit': COMMIT,
                'python_abi': sysconfig.get_config_var('SOABI'), 'machine': platform.machine(),
                'camss_capability': 'x1p-normal-world-v1', 'meson_options': options,
                'patches': {name: hashlib.sha256((ROOT / 'patches' / name).read_bytes()).hexdigest()
                            for name in PATCHES}}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    smoke(output)
    # Preserve the exact patched source for source redistribution with releases.
    run('tar', '--exclude=.git', '-czf', str(output.parent / 'libcamera-patched-source.tar.gz'),
        '-C', str(checkout), '.')


def smoke(output):
    output = Path(output).resolve()
    libs = output / 'build/src/libcamera'
    env = {'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8',
           'LD_LIBRARY_PATH': f'{libs}:{libs / "base"}',
           'XDG_CONFIG_HOME': str(output / 'opt-in'),
           'LIBCAMERA_IPA_MODULE_PATH': str(output / 'build/src/ipa/simple'),
           'LIBCAMERA_IPA_PROXY_PATH': str(output / 'build/src/libcamera/proxy/worker')}
    for path in output.rglob('*'):
        if path.is_file() and path.read_bytes()[:4] == b'\x7fELF':
            result = subprocess.check_output(['ldd', str(path)], env=env, text=True)
            if 'not found' in result:
                raise RuntimeError(result)
            for line in result.splitlines():
                if 'libcamera' in line and '=>' in line and str(libs) not in line:
                    raise RuntimeError('system libcamera leaked into bundled runtime: ' + line)
    code = ('import sys; sys.path.insert(0, sys.argv[1]); import libcamera; '
            'manager = libcamera.CameraManager.singleton(); '
            'print(libcamera.__file__, [camera.id for camera in manager.cameras])')
    run(sys.executable, '-I', '-B', '-c', code, str(output / 'build/src/py'), env=env)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--work', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--source', default=os.environ.get('FACELOCK_CAMERA_SOURCE'), help='local Git mirror containing the pinned commit')
    ap.add_argument('--jobs', type=int, default=2)
    args = ap.parse_args()
    if not 1 <= args.jobs <= 64:
        ap.error('--jobs must be 1..64')
    build(args.work, args.output, args.source, args.jobs)
