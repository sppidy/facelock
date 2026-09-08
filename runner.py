#!/usr/bin/env python3
"""Isolated launcher with an explicit camera runtime and a bounded worker."""
import argparse
import os
from pathlib import Path
import runpy
import signal
import stat
import subprocess
import sys
import sysconfig

HERE = Path(__file__).resolve().parent


def trusted(path):
    # Bootstrap before importing even the local facelock package.
    path = Path(path).absolute()
    resolved = path.resolve(strict=True)
    for candidate in (path, *path.parents, resolved, *resolved.parents):
        info = candidate.lstat()
        if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
            raise PermissionError(f"untrusted privileged runtime: {candidate}")
    return resolved


def runtime_environment(cfg, privileged):
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    for key in ("PAM_USER", "PAM_TYPE", "PAM_SERVICE", "SUDO_USER"):
        if key in os.environ:
            env[key] = os.environ[key]
    stack = cfg["runtime"]["stack"]
    if not privileged:
        stack = os.environ.get("FACELOCK_STAGED") or stack
    extra_python = None
    if stack:
        root = Path(stack)
        libraries = root / "build/src/libcamera"
        extra_python = root / "build/src/py"
        ipa = root / "build/src/ipa/simple"
        workers = root / "build/src/libcamera/proxy/worker"
        routing = root / "opt-in"
        for path in (libraries, extra_python / "libcamera/__init__.py", ipa,
                     routing / "libcamera/configuration.yaml", workers / "soft_ipa_proxy"):
            if not path.exists():
                raise FileNotFoundError(f"incomplete camera stack: {path}")
        binding = extra_python / "libcamera" / ("_libcamera" + sysconfig.get_config_var("EXT_SUFFIX"))
        if not list(libraries.glob("libcamera.so*")) or not binding.is_file():
            raise ValueError("camera stack is missing its library or matching Python binding")
        if privileged:
            trusted(root)
            for path in root.rglob("*"):
                trusted(path)
        env.update(LD_LIBRARY_PATH=f"{libraries}:{libraries / 'base'}", LIBCAMERA_IPA_MODULE_PATH=str(ipa),
                   LIBCAMERA_IPA_PROXY_PATH=str(workers), XDG_CONFIG_HOME=str(routing))
        if not privileged:
            env["FACELOCK_STAGED"] = str(root)
    tuning = cfg["runtime"]["tuning"]
    if tuning:
        path = Path(tuning)
        if privileged:
            trusted(path)
            for item in path.rglob("*"):
                trusted(item)
        elif not path.is_dir():
            raise FileNotFoundError(tuning)
        env["LIBCAMERA_IPA_CONFIG_PATH"] = str(path)
    return env, extra_python


def stop_worker(worker):
    if worker.poll() is None:
        try:
            os.killpg(worker.pid, signal.SIGTERM)
        except ProcessLookupError:
            worker.wait()
            return
        try:
            worker.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(worker.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            worker.wait()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    worker_mode = bool(argv and argv[0] == "--worker")
    if worker_mode:
        argv.pop(0)
    if not argv:
        raise ValueError("usage: facelock-run /usr/lib/facelock/{enroll,verify,diagnose}.py [options]")
    entry = Path(argv.pop(0)).resolve(strict=True)
    privileged = os.geteuid() == 0
    if entry.parent != HERE or entry.name not in ("verify.py", "enroll.py", "diagnose.py"):
        raise ValueError("choose an installed enroll.py, verify.py or diagnose.py entry point")
    if privileged:
        trusted(HERE)
        for path in HERE.rglob("*"):
            trusted(path)
    sys.path.insert(0, str(HERE))
    from facelock import config
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--config", default="/etc/facelock/config.yaml")
    ap.add_argument("--pam", action="store_true")
    args, _ = ap.parse_known_args(argv)
    if args.pam and (entry.name != "verify.py" or args.config != "/etc/facelock/config.yaml"):
        raise PermissionError("PAM uses the installed verification policy")
    if privileged:
        trusted(args.config)
    cfg = config.load(args.config)
    env, extra_python = runtime_environment(cfg, privileged)
    if worker_mode:
        if extra_python:
            sys.path.insert(0, str(extra_python))
        sys.argv = [str(entry), *argv]
        runpy.run_path(str(entry), run_name="__main__")
        return 0
    command = [sys.executable, "-I", str(HERE / "runner.py"), "--worker", str(entry), *argv]
    worker = subprocess.Popen(command, env=env, start_new_session=True)
    old_handlers = {}
    def interrupted(signum, frame):
        raise InterruptedError("facelock interrupted")
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.signal(sig, interrupted)
        timeout = (cfg["enrollment"]["timeout_sec"] if entry.name == "enroll.py"
                   else cfg["pam"]["verify_timeout_sec"])
        return worker.wait(timeout=timeout)
    finally:
        stop_worker(worker)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        # Also extinguish illumination if a native call required SIGKILL.
        if "ir" in cfg["auth"]["required_sensors"]:
            for key in ("strobe_path", "path"):
                try:
                    Path(cfg["ir_led"][key]).write_text("0")
                except OSError:
                    pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"facelock: {exc}", file=sys.stderr)
        sys.exit(1)
