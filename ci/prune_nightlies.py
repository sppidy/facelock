#!/usr/bin/env python3
"""Retain only the newest public nightly prereleases and their tags."""
import json
import os
import subprocess

KEEP = 5


def stale_nightlies(releases, keep=KEEP):
    nightlies = [release for release in releases
                 if not release['draft'] and release['prerelease']
                 and release['tag_name'].startswith('nightly-')]
    nightlies.sort(key=lambda release: release['published_at'], reverse=True)
    return [release['tag_name'] for release in nightlies[keep:]]


def main():
    repo = os.environ['GH_REPO']
    pages = json.loads(subprocess.check_output(
        ['gh', 'api', '--paginate', '--slurp', f'repos/{repo}/releases?per_page=100'],
        text=True))
    releases = [release for page in pages for release in page]
    stale = stale_nightlies(releases)
    for tag in stale:
        subprocess.run(['gh', 'release', 'delete', tag, '--repo', repo,
                        '--yes', '--cleanup-tag'], check=True)
    print(f'pruned {len(stale)} nightly release(s); retained {KEEP}')


if __name__ == '__main__':
    main()
