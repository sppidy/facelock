"""Inspect package metadata and an explicit payload allowlist without installing."""
import ast
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile


def inspect_payload(archive, deb=False):
    files = {}
    for member in archive.getmembers():
        name = member.name.removeprefix('./').rstrip('/')
        assert not name.startswith('/') and '..' not in Path(name).parts, name
        assert member.isdir() or member.isfile(), name
        if member.isdir():
            if name == 'var/lib/facelock':
                assert member.mode == 0o750
            continue
        data = archive.extractfile(member).read()
        files[name] = data
        if name in {'.PKGINFO', '.BUILDINFO', '.MTREE', '.INSTALL'} and not deb:
            continue
        allowed = (
            name in {'usr/bin/facelock-run', 'usr/bin/facelock-detect',
                     'usr/bin/facelock-pam-enable', 'etc/facelock/config.yaml',
                     'usr/lib/udev/rules.d/99-facelock-ir-led.rules'}
            or (name.startswith('usr/lib/facelock/') and name.endswith(('.py', '/pam_check.sh')))
            or (name.startswith('usr/share/facelock/profiles/') and name.endswith('.yaml'))
            or (name.startswith('usr/share/doc/facelock/') and Path(name).name in {
                'README.md', 'README.md.gz', 'config.example.yaml', 'changelog.Debian.gz'})
        )
        assert allowed, f'Unexpected payload: {name}'
        executable = name.startswith('usr/bin/') or name in {
            'usr/lib/facelock/setup_models.py', 'usr/lib/facelock/enroll.py',
            'usr/lib/facelock/verify.py', 'usr/lib/facelock/pam_check.sh'}
        assert member.mode == (0o755 if executable else 0o644), (name, oct(member.mode))
        assert member.uid == member.gid == 0, name
        if name.endswith('.py') or name == 'usr/bin/facelock-detect':
            ast.parse(data, filename=name)
        elif data.startswith(b'#!/bin/sh'):
            subprocess.run(['sh', '-n'], input=data, check=True)
    for required in ['usr/bin/facelock-run', 'usr/lib/facelock/pam_check.sh',
                     'usr/lib/facelock/verify.py', 'usr/lib/facelock/facelock/__init__.py',
                     'etc/facelock/config.yaml']:
        assert required in files, required
    assert b'exec /usr/bin/python3 ' in files['usr/bin/facelock-run']
    assert b'$LIBDIR/../../bin/facelock-run' in files['usr/lib/facelock/pam_check.sh']
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
    db = tarfile.open(dbs[0].resolve())
    names = {n.removeprefix('./').rstrip('/') for n in db.getnames()}
    entry = next((n for n in names if n.endswith('/desc')), None)
    assert entry is not None, 'no desc entry in repo db'
    desc = db.extractfile(db.getmember('./' + entry if './' + entry in db.getnames() else entry)).read().decode()
    assert f'facelock-{version}-any.pkg.tar.zst' in desc, desc[:400]
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
    for field in ['pkgname = facelock', f'pkgver = {version}', 'arch = any',
                  'backup = etc/facelock/config.yaml', 'depend = python']:
        assert field in info.splitlines(), field
    for name in debfiles.keys() & archfiles.keys():
        assert debfiles[name] == archfiles[name], f'Payload differs: {name}'
    print(metadata + info)


if __name__ == '__main__':
    main()
