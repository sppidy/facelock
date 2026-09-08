"""Classify the entire push, then freeze the exact checkout for both builds."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def stable_push(event_name, ref, before, after):
    if event_name != 'push' or ref != 'refs/heads/main':
        return False
    # A new branch has no previous tree; treat all initial files as additions.
    if not before or set(before) == {'0'}:
        before = subprocess.check_output(
            ['git', 'hash-object', '-t', 'tree', '--stdin'], input=b'').decode().strip()
    changed = subprocess.check_output(
        ['git', 'diff', '--name-only', '--no-renames', before, after, '--', 'PKGBUILD'])
    return changed.strip() == b'PKGBUILD'


def release_history(stable, sha):
    prefix = 'v' if stable else 'nightly-'
    tags = subprocess.check_output(
        ['git', 'tag', '--merged', sha, '--sort=-creatordate'], text=True).splitlines()
    previous = next((tag for tag in tags if tag.startswith(prefix)), None)
    revision = f'{previous}..{sha}' if previous else sha
    fields = subprocess.check_output(
        ['git', 'log', '-z', '--reverse', '--format=%H%x00%s', revision],
        text=True).split('\0')
    changes = [{'commit': fields[i], 'subject': fields[i + 1]}
               for i in range(0, len(fields) - 1, 2) if fields[i]]
    return previous, changes


def main():
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    sha = os.environ['GITHUB_SHA']
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip() == sha
    stable = stable_push(os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REF'],
                         event.get('before'), sha)
    recipe = Path('PKGBUILD').read_text()
    version = re.search(r'^pkgver=([0-9][0-9a-zA-Z.]*)$', recipe, re.M).group(1)
    release = re.search(r'^pkgrel=([0-9]+)$', recipe, re.M).group(1)
    run = os.environ['GITHUB_RUN_ID']
    attempt = os.environ['GITHUB_RUN_ATTEMPT']
    if not stable:
        version += f'.r{run}.a{attempt}.g{sha[:12]}'
    tag = f'v{version}-{release}' if stable else f'nightly-{version}-{release}'
    package_name = 'facelock' if stable else 'facelock-nightly'
    previous_tag, changes = release_history(stable, sha)
    dist = Path('dist')
    dist.mkdir()  # Refuse stale output from another build.
    archive = dist / 'facelock-source.tar'
    subprocess.run(['git', 'archive', '--format=tar', '--prefix=facelock/',
                    '-o', str(archive), sha], check=True)
    metadata = dict(commit=sha, version=version, pkgrel=release, tag=tag, stable=stable,
                    package_name=package_name, previous_tag=previous_tag, changes=changes,
                    source_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                    repository=os.environ['GITHUB_REPOSITORY'], run_id=run, attempt=attempt,
                    run_url=f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run}",
                    source_date_epoch=subprocess.check_output(
                        ['git', 'show', '-s', '--format=%ct', sha]).decode().strip())
    (dist / 'provenance.json').write_text(json.dumps(metadata, indent=2) + '\n')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'tag={tag}\nstable={str(stable).lower()}\n')


if __name__ == '__main__':
    main()
