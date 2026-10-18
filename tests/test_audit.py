"""Integration tests for `patch` levels/JSON/exit codes, `picks`, `merge`, and `delta`."""

import json
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


def test_picks(repo):
    result = invoke('picks', '-j', 'gcs', 'cloud')
    assert result.exit_code == 1
    records = json.loads(result.stdout)
    assert sorted(
        [(r['commit'], r['source'], r['class'], r['level'], r['ok'], [(f['path'], f['status']) for f in r['files']]) for r in records],
    ) == sorted([
        (repo['g7'], 'deadbeefdeadbeef', 'missing', None, True, []),
        (repo['g6'], repo['c6'], 'files-differ', None, False, [('y.txt', 'right-only')]),
        (repo['g5'], repo['c5'], 'equal', 'none', True, []),
        (repo['c2'], repo['g1'], 'equal@lines', 'lines', False, [('app.py', 'differs')]),
    ])
    assert result.stderr.strip().split('\n')[-1] == '4 pick(s): 1 equal, 1 equal@lines, 1 files-differ, 1 missing'


def test_picks_threshold(repo):
    """At `-n lines`, only the adapted pick fails."""
    result = invoke('picks', '-n', 'lines', 'gcs', 'cloud')
    assert result.exit_code == 1
    assert result.stdout.split('\n') == [
        f"{repo['g7'][:10]} missing       deadbeefde g7",
        f"{repo['g6'][:10]} files-differ  {repo['c6'][:10]} g6",
        "    right-only    y.txt",
        '',
    ]


def test_patch_levels_and_exit(repo):
    left, right = f"{repo['g1']}^..{repo['g1']}", f"{repo['c2']}^..{repo['c2']}"
    result = invoke('patch', '--pager', 'never', '-j', left, right)
    assert result.exit_code == 1
    assert json.loads(result.stdout) == [
        {'path': 'app.py', 'status': 'differs', 'level': 'lines', 'left_lines': 2, 'right_lines': 2},
    ]
    result = invoke('patch', '--pager', 'never', '-n', 'lines', left, right)
    assert result.exit_code == 0
    assert result.stderr.split('\n') == ['', '1 equal@lines', 'No differences in patches', '']


def test_patch_display_strips_index(repo):
    """The diff of diffs shows real differences, not `index` noise."""
    result = invoke('patch', '--pager', 'never', '--color', 'never', f"{repo['g4']}^..{repo['g4']}", f"{repo['c4']}^..{repo['c4']}")
    assert result.exit_code == 1
    assert result.stdout.split('\n') == [
        '',
        '=' * 60,
        'File: sig.ts',
        '=' * 60,
        f"--- sig.ts in {repo['g4']}^..{repo['g4']}",
        f"+++ sig.ts in {repo['c4']}^..{repo['c4']}",
        '@@ -4,5 +4,5 @@',
        ' +++ b/sig.ts',
        ' @@ -1,2 +1,2 @@',
        ' -function f(a, b) {',
        '-+function f(a, b, staged = true) {',
        '++function f(a, b, c) {',
        '  }',
        '',
    ]


def merge_files(record):
    return sorted((f['path'], f['status'], f['level']) for f in record['files'])


def test_merge(repo):
    result = invoke('merge', '-j', '-n', 'tokens', repo['merge'])
    assert result.exit_code == 0
    [record] = json.loads(result.stdout)
    # c6 (named by g6's trailer), c5 (named by g5's), c2 (whose trailer names g1); newest first
    assert record['absorbed_commits'] == [repo['c6'], repo['c5'], repo['c2']]
    assert merge_files(record) == [
        ('README', 'absorbed', None),
        ('adopt.md', 'adopted', None),
        ('app.py', 'absorbed', None),
        ('own.txt', 'equal', 'none'),
        ('sig.ts', 'equal', 'tokens'),
        ('x.txt', 'absorbed', None),
        ('y.txt', 'equal', 'none'),
        ('z.txt', 'equal', 'none'),
    ]
    assert (record['before']['files'], record['after']['files']) == (8, 4)


def test_merge_default_level_flags_tokens(repo):
    result = invoke('merge', repo['merge'])
    assert result.exit_code == 1
    assert result.stdout.split('\n') == [
        f"{repo['merge'][:10]} Merge cloud into gcs",
        '  delta: 8 files +9/-6 → 4 files +4/-3',
        '  absorbed picks: 3',
        '  3 equal, 1 equal@tokens, 3 absorbed, 1 adopted',
        '    equal@tokens  sig.ts',
        f"  git-didi patch {repo['base'][:10]}..{repo['g7'][:10]} {repo['c6'][:10]}..{repo['merge'][:10]}",
        '',
    ]


def test_delta_four_refs_matches_merge(repo):
    """`delta CHILD_A PARENT_A CHILD_B PARENT_B` around the merge == `merge M`."""
    result = invoke('delta', '-j', '-n', 'tokens', repo['g7'], repo['c6'], repo['merge'], 'cloud')
    assert result.exit_code == 0
    [record] = json.loads(result.stdout)
    [merge_record] = json.loads(invoke('merge', '-j', '-n', 'tokens', repo['merge']).stdout)
    assert merge_files(record) == merge_files(merge_record)
    assert (record['before'], record['after']) == (merge_record['before'], merge_record['after'])


def test_usage_error_exit_2(repo):
    assert invoke('patch', 'HEAD').exit_code == 2
