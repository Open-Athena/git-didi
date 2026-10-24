"""Shared fixtures: a parent/child repo with picks flowing both ways, and a CLI runner."""

import subprocess
import sys
from dataclasses import dataclass

import pytest


def git(cwd, *args, check=True) -> str:
    result = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def write(repo, **files):
    for name, content in files.items():
        (repo / name.replace('__', '.')).write_text(content)


def commit(repo, msg, **files) -> str:
    write(repo, **files)
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', msg)
    return git(repo, 'rev-parse', 'HEAD')


APP = ''.join(f'line{i:02}\n' for i in range(1, 21))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """`cloud` (parent) and `gcs` (child) diverge from `base`; picks flow both ways, then `cloud` merges into `gcs`.

    gcs own commits:
        g1  app.py: line05 → LINE05              (cloud picks it as c2: absorbed)
        g2  adopt.md                             (cloud copies it in c3, no trailer: adopted)
        g3  own.txt                              (stays: equal)
        g4  sig.ts: f(a, b) → f(a, b, staged = true)  (cloud also edits the line: equal@tokens)
        g5  pick -x of c5 (README)               (identical pick; absorbed by the merge)
        g6  "pick" of c6 that also touches y.txt (files-differ; x.txt absorbed)
        g7  trailer names a missing commit       (missing)
    cloud:
        c1  app.py: prepend 2 header lines
        c2  pick -x of g1                        (equal@lines vs. g1)
        c3  adopt.md + other.txt
        c4  sig.ts: f(a, b) → f(a, b, c)
        c5  README fix
        c6  x.txt
    """
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.chdir(repo)
    git(repo, 'init', '-q', '-b', 'cloud')
    git(repo, 'config', 'user.email', 'test@test.com')
    git(repo, 'config', 'user.name', 'Test')
    git(repo, 'config', 'core.hooksPath', '/dev/null')
    shas = {}
    shas['base'] = commit(
        repo, 'base',
        app__py=APP, sig__ts='function f(a, b) {\n}\n', README='readme\n',
        x__txt='x\n', y__txt='y\n', z__txt='z\n',
    )
    git(repo, 'checkout', '-qb', 'gcs')
    shas['g1'] = commit(repo, 'g1', app__py=APP.replace('line05', 'LINE05'))
    shas['g2'] = commit(repo, 'g2', adopt__md='hello\nworld\n')
    shas['g3'] = commit(repo, 'g3', own__txt='gcs only\n')
    shas['g4'] = commit(repo, 'g4', sig__ts='function f(a, b, staged = true) {\n}\n')

    git(repo, 'checkout', '-q', 'cloud')
    shas['c1'] = commit(repo, 'c1', app__py='header1\nheader2\n' + APP)
    git(repo, 'cherry-pick', '-x', shas['g1'])
    shas['c2'] = git(repo, 'rev-parse', 'HEAD')
    shas['c3'] = commit(repo, 'c3', adopt__md='hello\nworld\n', other__txt='other\n')
    shas['c4'] = commit(repo, 'c4', sig__ts='function f(a, b, c) {\n}\n')
    shas['c5'] = commit(repo, 'c5', README='readme fixed\n')
    shas['c6'] = commit(repo, 'c6', x__txt='x2\n')

    git(repo, 'checkout', '-q', 'gcs')
    git(repo, 'cherry-pick', '-x', shas['c5'])
    shas['g5'] = git(repo, 'rev-parse', 'HEAD')
    shas['g6'] = commit(repo, f"g6\n\n(cherry picked from commit {shas['c6']})", x__txt='x2\n', y__txt='y2\n')
    shas['g7'] = commit(repo, 'g7\n\n(cherry picked from commit deadbeefdeadbeef)', z__txt='z2\n')

    git(repo, 'merge', '-q', '--no-edit', 'cloud', check=False)
    write(repo, sig__ts='function f(a, b, c, staged = true) {\n}\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'Merge cloud into gcs')
    shas['merge'] = git(repo, 'rev-parse', 'HEAD')
    return shas


@dataclass
class Result:
    exit_code: int
    stdout: str
    stderr: str


def invoke(*args) -> Result:
    proc = subprocess.run([sys.executable, '-c', 'from didi.cli import cli; cli()', *args], capture_output=True, text=True)
    return Result(proc.returncode, proc.stdout, proc.stderr)
