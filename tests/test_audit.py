"""Integration tests for `patch` levels/JSON/exit codes, `picks`, `merge`, and `delta`."""

import json

from conftest import invoke


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
