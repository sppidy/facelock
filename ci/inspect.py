"""Inspect package metadata and an explicit payload allowlist without installing."""
import ast
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile


def inspect_payload(archive, deb=False, elf_machine=183):
    files = {}
    for member in archive.getmembers():
        name = member.name.removeprefix('./').rstrip('/')
        assert not name.startswith('/') and '..' not in Path(name).parts, name
        assert member.isdir() or member.isfile(), name
        if member.isdir():
            if name == 'var/lib/facelock':
                assert member.mode == 0o700
            continue
        data = archive.extractfile(member).read()
        files[name] = data
        if name in {'.PKGINFO', '.BUILDINFO', '.MTREE', '.INSTALL'} and not deb:
            continue
        if name.startswith('usr/lib/facelock/camera/'):
            assert member.uid == member.gid == 0, name
            assert member.mode in (0o644, 0o755), name
            if data.startswith(b'\x7fELF'):
                assert data[4] == 2 and data[5] == 1, name
                assert int.from_bytes(data[18:20], 'little') == elf_machine, name
            else:
                assert name.endswith(('.py', '.yaml', '.json', '.txt', '.rst', '.sign')) or '/LICENSES/' in name, name
            continue
        allowed = (
            name in {'usr/share/python3/runtime.d/facelock.rtupdate', 'usr/bin/facelock-run', 'usr/bin/facelock-detect',
                     'usr/bin/facelock-auth', 'usr/lib/systemd/system/facelock-auth.socket',
                     'usr/lib/systemd/system/facelock-auth@.service',
                     'usr/bin/facelock-feedback', 'usr/bin/facelock-enroll',
                     'usr/lib/systemd/user/facelock-feedback.service',
                     'usr/share/applications/io.github.sppidy.Facelock.desktop',
                     'usr/bin/facelock-pam-enable', 'etc/facelock/config.yaml',
                     'usr/lib/udev/rules.d/99-facelock-ir-led.rules'}
            or (name.startswith('usr/lib/facelock/') and name.endswith(('.py', '/pam_check.sh')))
            or (name.startswith(('usr/share/facelock/profiles/', 'usr/share/facelock/tuning/simple/')) and name.endswith('.yaml'))
            or (name.startswith('usr/share/doc/facelock/') and Path(name).name in {
                'README.md', 'README.md.gz', 'config.example.yaml', 'changelog.Debian.gz', 'changelog.gz'})
        )
        assert allowed, f'Unexpected payload: {name}'
        executable = name.startswith('usr/bin/') or name in {
            'usr/share/python3/runtime.d/facelock.rtupdate',
            'usr/lib/facelock/setup_models.py', 'usr/lib/facelock/enroll.py',
            'usr/lib/facelock/verify.py', 'usr/lib/facelock/pam_check.sh',
            'usr/lib/facelock/runner.py', 'usr/lib/facelock/diagnose.py',
            'usr/lib/facelock/calibrate.py'}
        assert member.mode == (0o755 if executable else 0o644), (name, oct(member.mode))
        assert member.uid == member.gid == 0, name
        if name.endswith('.py') or name in {'usr/bin/facelock-detect', 'usr/bin/facelock-auth',
                                            'usr/bin/facelock-feedback', 'usr/bin/facelock-enroll'}:
            ast.parse(data, filename=name)
        elif data.startswith(b'#!/bin/sh'):
            subprocess.run(['sh', '-n'], input=data, check=True)
    for required in ['usr/bin/facelock-run', 'usr/lib/facelock/pam_check.sh',
                     'usr/lib/facelock/verify.py', 'usr/lib/facelock/facelock/__init__.py',
                     'etc/facelock/config.yaml']:
        assert required in files, required
    assert b'/usr/bin/python3' in files['usr/bin/facelock-run']
    assert b'runpy' in files['usr/lib/facelock/runner.py']
    assert b'/usr/bin/env -i' in files['usr/bin/facelock-run']
    assert b'--pam --quiet' in files['usr/lib/facelock/pam_check.sh']
    assert b'$LIBDIR/../../bin/facelock-run' in files['usr/lib/facelock/pam_check.sh']
    assert b'--serve' in files['usr/lib/systemd/user/facelock-feedback.service']
    assert b'Exec=facelock-enroll' in files['usr/share/applications/io.github.sppidy.Facelock.desktop']
    for required in ('usr/bin/facelock-feedback', 'usr/bin/facelock-enroll',
                     'usr/bin/facelock-auth', 'usr/lib/facelock/facelock/broker.py',
                     'usr/lib/systemd/system/facelock-auth.socket',
                     'usr/lib/systemd/system/facelock-auth@.service',
                     'usr/lib/facelock/calibrate.py', 'usr/lib/facelock/facelock/stereo.py',
                     'usr/lib/facelock/facelock/depth.py',
                     'usr/lib/facelock/facelock/gui.py', 'usr/lib/facelock/facelock/wizard.py',
                     'usr/lib/facelock/facelock/drift.py', 'usr/lib/facelock/facelock/feedback.py'):
        assert required in files, required
    return files


def main():
    dist = Path(sys.argv[1])
    m = json.loads((dist / 'provenance.json').read_text())
    version = f"{m['version']}-{m['pkgrel']}"
    debs = list(dist.glob('*.deb'))
    arches = list(dist.glob('*.pkg.tar.zst'))
    assert len(debs) == len(arches) == 1
    dbs = list(dist.glob('facelock.db.tar.zst'))
    assert len(dbs) == 1, 'repo database missing'
    dbpath = dbs[0]
    if dbpath.is_symlink():
        # repo-add writes plain .db as the file and .db.tar.zst as a link
        dbpath = dbpath.resolve()
        if not dbpath.is_file():
            dbpath = dist / 'facelock.db'
    # Debian's pacman 7.0 repo-add may compress with zstd, which stdlib
    # tarfile cannot read. Verify with zstd if tarfile refuses.
    try:
        db = tarfile.open(dbpath)
    except tarfile.ReadError:
        raw = subprocess.run(['zstd', '-dc', str(dbpath)],
                             check=True, capture_output=True).stdout
        db = tarfile.open(fileobj=io.BytesIO(raw), mode='r')
    names = {n.removeprefix('./').rstrip('/') for n in db.getnames()}
    entry = next((n for n in names if n.endswith('/desc')), None)
    assert entry is not None, 'no desc entry in repo db'
    desc = db.extractfile(db.getmember('./' + entry if './' + entry in db.getnames() else entry)).read().decode()
    assert f'facelock-{version}-aarch64.pkg.tar.zst' in desc, desc[:400]
    assert '%FILENAME%' in desc and '%VERSION%' in desc
    metadata = subprocess.check_output(['dpkg-deb', '-f', str(debs[0]),
                                       'Package', 'Version', 'Architecture'], text=True)
    assert metadata == f'Package: facelock\nVersion: {version}\nArchitecture: arm64\n', metadata
    payload = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(debs[0])])
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        debfiles = inspect_payload(archive, deb=True)
    control = subprocess.check_output(['dpkg-deb', '--ctrl-tarfile', str(debs[0])])
    with tarfile.open(fileobj=io.BytesIO(control)) as archive:
        postinst = archive.extractfile('./postinst').read()
        subprocess.run(['sh', '-n'], input=postinst, check=True)
        assert not any(line.strip().startswith(b'facelock-pam-enable') for line in postinst.splitlines())
        assert b'/etc/pam.d' not in postinst
    payload = subprocess.check_output(['zstd', '-dc', str(arches[0])])
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        archfiles = inspect_payload(archive)
    info = archfiles['.PKGINFO'].decode()
    for field in ['pkgname = facelock', f'pkgver = {version}', 'arch = aarch64',
                  'backup = etc/facelock/config.yaml', 'depend = python']:
        assert field in info.splitlines(), field
    for name in debfiles.keys() & archfiles.keys():
        if not name.startswith('usr/lib/facelock/camera/'):
            assert debfiles[name] == archfiles[name], f'Payload differs: {name}'
    for payload in (debfiles, archfiles):
        base = 'usr/lib/facelock/camera/'
        manifest = json.loads(payload[base + 'manifest.json'])
        assert manifest['commit'] == '597a5bb97bf9257790edf21020c679aa666ba307'
        assert manifest['machine'] == 'aarch64'
        assert manifest['camss_capability'] == 'x1p-normal-world-v1'
        assert base + 'build/src/libcamera/proxy/worker/soft_ipa_proxy' in payload
        assert any(n.startswith(base + 'build/src/py/libcamera/_libcamera.') and n.endswith('.so') for n in payload)
    print(metadata + info)


if __name__ == '__main__':
    main()
