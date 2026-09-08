"""Build independent stable and nightly pacman repositories for GitHub Pages."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

REPO = 'facelock'
ARCH = 'aarch64'
SERVER_HINT = 'https://facelock-repo.sppidy.in'


def gh(*args):
    return subprocess.check_output(['gh', *args], text=True).strip()


def channel_releases(releases):
    available = [release for release in releases if not release['draft'] and any(
        asset['name'].endswith('.pkg.tar.zst') for asset in release['assets'])]
    available.sort(key=lambda release: release['published_at'], reverse=True)
    stable = next(release for release in available if not release['prerelease'] and any(
        asset['name'].startswith('facelock-') for asset in release['assets']))
    nightly = next(release for release in available
                   if release['prerelease'] and release['tag_name'].startswith('nightly-')
                   and any(asset['name'].startswith('facelock-nightly-')
                           for asset in release['assets']))
    return {'stable': stable, 'nightly': nightly}


def package_for_release(release, current, dist, destination):
    destination.mkdir(parents=True)
    if release['tag_name'] == current['tag']:
        packages = list(dist.glob('*.pkg.tar.zst'))
        assert len(packages) == 1
        target = destination / packages[0].name
        shutil.copy2(packages[0], target)
        return target
    gh('release', 'download', release['tag_name'], '--repo', current['repository'],
       '--pattern', '*.pkg.tar.zst', '--dir', str(destination))
    packages = list(destination.glob('*.pkg.tar.zst'))
    assert len(packages) == 1, release['tag_name']
    return packages[0]


def build_repository(directory, package):
    subprocess.run(['repo-add', str(directory / f'{REPO}.db.tar.zst'), str(package)], check=True)
    for name in (f'{REPO}.db', f'{REPO}.files'):
        alias = directory / name
        compressed = directory / f'{name}.tar.zst'
        source = compressed.resolve() if compressed.is_symlink() else compressed
        if alias.exists() or alias.is_symlink():
            alias.unlink()
        shutil.copy2(source, alias)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (directory / 'SHA256SUMS').write_text(f'{digest}  {package.name}\n')
    listing = '\n'.join(f"<li><a href='{path.name}'>{path.name}</a></li>"
                        for path in sorted(directory.iterdir()) if path.is_file())
    channel = directory.parent.name
    (directory / 'index.html').write_text(
        f'<html><head><title>facelock {channel}</title></head><body>'
        f'<h1>facelock {channel} repository</h1><pre>[facelock-{channel}]\n'
        f'Server = {SERVER_HINT}/{channel}/$arch</pre><ul>{listing}</ul></body></html>')


def main():
    dist = Path('dist')
    current = json.loads((dist / 'provenance.json').read_text())
    assert current['repository'] == os.environ['GH_REPO']
    releases = json.loads(gh('api', f"repos/{current['repository']}/releases?per_page=100"))
    published = json.loads(gh('api',
                              f"repos/{current['repository']}/releases/tags/{current['tag']}"))
    releases = [release for release in releases if release['tag_name'] != current['tag']]
    releases.append(published)
    selected = channel_releases(releases)
    channel = 'stable' if current['stable'] else 'nightly'
    assert selected[channel]['tag_name'] == current['tag']
    site = Path('site')
    if site.exists():
        shutil.rmtree(site)
    for channel, release in selected.items():
        directory = site / channel / ARCH
        package = package_for_release(release, current, dist, directory)
        build_repository(directory, package)
    site.joinpath('index.html').write_text(
        "<html><head><title>facelock repositories</title></head><body>"
        f"<h1>facelock repositories</h1><ul><li><a href='stable/{ARCH}/'>stable</a></li>"
        f"<li><a href='nightly/{ARCH}/'>nightly</a></li></ul></body></html>")
    print('site ready: independent stable and nightly repositories')


if __name__ == '__main__':
    main()
