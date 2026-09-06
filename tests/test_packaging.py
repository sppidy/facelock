import ast
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from ci.prepare import stable_push

ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_classification(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], text=True).strip()
            git('init', '-q')
            def commit(file, content):
                Path(directory, file).write_text(content)
                git('add', '.')
                git('-c', 'user.name=Test', '-c', 'user.email=test@example.org',
                    '-c', 'commit.gpgsign=false', 'commit', '-qm', 'test')
                return git('rev-parse', 'HEAD')
            first = commit('PKGBUILD', 'one')
            second = commit('PKGBUILD', 'two')
            third = commit('README.md', 'docs')
            previous = Path.cwd()
            try:
                os.chdir(directory)
                self.assertTrue(stable_push('push', 'refs/heads/main', first, third))
                self.assertFalse(stable_push('push', 'refs/heads/main', second, third))
                for event, ref in [('schedule', 'refs/heads/main'),
                                   ('workflow_dispatch', 'refs/heads/main'),
                                   ('push', 'refs/tags/v1'), ('push', 'refs/heads/topic')]:
                    self.assertFalse(stable_push(event, ref, first, third))
                self.assertTrue(stable_push('push', 'refs/heads/main', '0' * 40, third))
                reverted = commit('PKGBUILD', 'one')
                self.assertFalse(stable_push('push', 'refs/heads/main', first, reverted))
            finally:
                os.chdir(previous)

    def test_syntax_and_recipe(self):
        for path in list(ROOT.glob('*.py')) + list((ROOT / 'facelock').glob('*.py')) + list((ROOT / 'ci').glob('*.py')) + [ROOT / 'facelock-detect']:
            ast.parse(path.read_text(), filename=str(path))
        for path in list(ROOT.glob('*.sh')) + list((ROOT / 'ci').glob('*.sh')) + [ROOT / 'PKGBUILD', ROOT / 'facelock.install', ROOT / 'facelock-run', ROOT / 'facelock-pam-enable', ROOT / 'debian/postinst']:
            subprocess.run(['bash', '-n', str(path)], check=True)
        tag = subprocess.check_output(['bash', '-c', 'source PKGBUILD; printf "%s" "$_gittag"'], cwd=ROOT, text=True)
        self.assertEqual(tag, 'v0.1.0-2')
        result = subprocess.run(['bash', '-ec', 'source PKGBUILD'], cwd=ROOT,
                                env={**os.environ, 'FACELOCK_SOURCE_ARCHIVE': 'source.tar'}, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
