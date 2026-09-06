"""Never overwrite a release; verify draft assets before making them public."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True).strip()


def main():
    dist = Path('dist')
    metadata = json.loads((dist / 'provenance.json').read_text())
    tag = os.environ['RELEASE_TAG']
    stable = os.environ['RELEASE_STABLE'] == 'true'
    assert metadata['commit'] == os.environ['GITHUB_SHA']
    assert metadata['tag'] == tag and metadata['stable'] == stable
    assert metadata['run_id'] == os.environ['GITHUB_RUN_ID']
    assert metadata['attempt'] == os.environ['GITHUB_RUN_ATTEMPT']
    subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=dist, check=True)
    # The refs listing must succeed: network/auth failures are not "tag absent".
    refs = json.loads(gh('api', f'repos/{os.environ["GH_REPO"]}/git/matching-refs/tags/{tag}'))
    if any(ref['ref'] == f'refs/tags/{tag}' for ref in refs):
        raise SystemExit(f'Refusing existing tag {tag}; bump pkgrel for a new stable release.')
    notes = (f"Commit: {metadata['commit']}\n\nBuild: {metadata['run_url']}\n\n"
             'Native ARM64 Debian trixie build. Arch payload is architecture-independent (any). '
             'SHA256SUMS covers packages, source archive and build provenance. '
             'Packaging checks only, not hardware authentication certification. '
             'PAM activation is manual; retain password login and test in a second session.')
    args = ['release', 'create', tag, '--target', metadata['commit'], '--draft',
            '--title', f'Facelock {tag}', '--notes', notes]
    if not stable:
        args.append('--prerelease')
    gh(*args, *(str(p) for p in sorted(dist.iterdir())))
    with tempfile.TemporaryDirectory() as temp:
        gh('release', 'download', tag, '--dir', temp)
        remote = Path(temp)
        assert {p.name for p in remote.iterdir()} == {p.name for p in dist.iterdir()}
        for local in dist.iterdir():
            assert hashlib.sha256(local.read_bytes()).digest() == hashlib.sha256(
                (remote / local.name).read_bytes()).digest(), local.name
    gh('release', 'edit', tag, '--draft=false', '--latest=' + str(stable).lower())
    ref = json.loads(gh('api', f'repos/{os.environ["GH_REPO"]}/git/ref/tags/{tag}'))
    assert ref['object']['sha'] == metadata['commit']
    release = json.loads(gh('release', 'view', tag, '--json', 'url,isDraft,isPrerelease'))
    assert not release['isDraft'] and release['isPrerelease'] == (not stable)
    print(release['url'])


if __name__ == '__main__':
    main()
