"""Never overwrite a release; verify draft assets before making them public."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True).strip()


def release_notes(metadata):
    repo_url = f"https://github.com/{metadata['repository']}"
    previous = metadata.get('previous_tag')
    heading = f"## Changes since `{previous}`" if previous else '## Changes'
    changes = metadata.get('changes', [])
    lines = [heading, '']
    if changes:
        lines.extend(f"- [`{change['commit'][:7]}`]({repo_url}/commit/{change['commit']}) {change['subject']}"
                     for change in changes)
    else:
        lines.append('No source changes since the previous release.')
    lines.extend(['', '## Build details', '',
                  f"- Commit: [`{metadata['commit'][:12]}`]({repo_url}/commit/{metadata['commit']})",
                  f"- [Native ARM64 package build]({metadata['run_url']})",
                  '- Debian trixie and Arch Linux ARM each compile their own private patched libcamera runtime.',
                  '- PAM activation remains manual. Keep password login available while testing.'])
    return '\n'.join(lines)


def release_assets(dist):
    packages = sorted(dist.glob('*.deb')) + sorted(dist.glob('*.pkg.tar.zst'))
    assets = packages + [dist / 'libcamera-patched-source.tar.gz', dist / 'SHA256SUMS']
    assert len(packages) == 2 and all(path.is_file() for path in assets), assets
    return assets


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
    notes = release_notes(metadata)
    args = ['release', 'create', tag, '--target', metadata['commit'], '--draft',
            '--title', f'Facelock {tag}', '--notes', notes]
    if not stable:
        args.append('--prerelease')
    assets = release_assets(dist)
    gh(*args, *(str(p) for p in assets))
    with tempfile.TemporaryDirectory() as temp:
        gh('release', 'download', tag, '--dir', temp)
        remote = Path(temp)
        assert {p.name for p in remote.iterdir()} == {p.name for p in assets}
        for local in assets:
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
