#!/usr/bin/env python3
"""Copy only the matched camera runtime from a build; never update an existing snapshot."""
import argparse
from pathlib import Path
import shutil
import sysconfig


def stage(source, target):
    source, target = Path(source), Path(target)
    if target.exists():
        raise FileExistsError(f"snapshot already exists: {target}")
    files = []
    for relative in ("build/src/libcamera", "build/src/libcamera/base"):
        files += [p for p in (source / relative).glob("*.so*") if p.is_file()]
    files += [p for p in (source / "build/src/ipa/simple").glob("*.so*") if p.is_file()]
    worker = source / "build/src/libcamera/proxy/worker/soft_ipa_proxy"
    files += [worker]
    binding = source / "build/src/py/libcamera" / ("_libcamera" + sysconfig.get_config_var("EXT_SUFFIX"))
    files += [binding, source / "build/src/py/libcamera/__init__.py",
              source / "opt-in/libcamera/configuration.yaml"]
    # utils is a build-tree symlink. Enumerate actual Python files, copy bytes.
    utils = source / "build/src/py/libcamera/utils"
    files += list(utils.rglob("*.py"))
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
    target.mkdir(parents=True, mode=0o755)
    for path in files:
        dest = target / path.relative_to(source)
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copyfile(path, dest)
        dest.chmod(0o755 if path == worker else 0o644)
    # Root-owned snapshots are suitable for runtime.stack after administrator
    # review/installation. An unprivileged snapshot is diagnostic-only.
    return target


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("target", type=Path)
    args = ap.parse_args()
    print(stage(args.source, args.target))


if __name__ == "__main__":
    main()
