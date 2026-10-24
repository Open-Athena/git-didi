"""Tests for `git-didi report`, on the `conftest.repo` fixture (`cloud` parent, `gcs` child)."""

import json
from pathlib import Path

import jsonschema
import pytest

from conftest import invoke
from didi.report import load_config

SCHEMA = json.loads((Path(__file__).parent.parent / 'www/schema.json').read_text())

DIDI_YML = """\
repo_url: https://github.com/example/repo
lanes:
  cloud: {label: base, color: '#5b5b5b'}
  gcs: {parent: cloud, color: '#4285f4', aliases: [gcs-old], surfaces: {intrinsic: [own.txt]}}
"""


@pytest.fixture
def report(repo, tmp_path):
    cfg = tmp_path / 'didi.yml'
    cfg.write_text(DIDI_YML)
    out = tmp_path / 'report.json'
    result = invoke('report', '-c', str(cfg), '-o', str(out))
    assert result.exit_code == 0, result.stderr
    assert result.stderr.split('\n') == [
        'picks: 0 reused, 4 to classify',
        'merges: 0 reused, 1 to check',
        'pairs: 1 lane pair(s)',
        f'Wrote {out}: 2 lanes, 4 picks, 1 merges',
        '',
    ]
    return {'repo': repo, 'cfg': cfg, 'out': out, 'data': json.loads(out.read_text())}


def test_report_schema(report):
    jsonschema.validate(report['data'], SCHEMA)


def seg_shape(seg: dict) -> tuple:
    """A segment, minus dates: (kind, from, SHAs, class(es))."""
    if seg['kind'] == 'run':
        return ('run', [c['sha'] for c in seg['commits']])
    if seg['kind'] == 'merge':
        return ('merge', seg['from_lane'], seg['from_kind'], seg['sha'], seg.get('class'))
    return ('cp', seg['from_lane'], [(p['sha'], p['source'], p['class']) for p in seg['picks']])


def test_report_segments(report):
    r, segs = report['repo'], report['data']['segments']
    assert {lane: [seg_shape(s) for s in ss] for lane, ss in segs.items()} == {
        'cloud': [
            ('run', [r['base'], r['c1']]),
            ('cp', 'gcs', [(r['c2'], r['g1'], 'equal@lines')]),
            ('run', [r['c3'], r['c4'], r['c5'], r['c6']]),
        ],
        'gcs': [
            ('run', [r['g1'], r['g2'], r['g3'], r['g4']]),
            ('cp', 'cloud', [(r['g5'], r['c5'], 'equal'), (r['g6'], r['c6'], 'files-differ')]),
            ('cp', 'missing', [(r['g7'], 'deadbeefdeadbeef', 'missing')]),
            ('merge', 'cloud', 'parent', r['merge'], 'equal@tokens'),
        ],
    }


def test_report_lanes(report):
    r, lanes = report['repo'], report['data']['lanes']
    assert [{k: v for k, v in lane.items() if k not in ('head_date', 'fork_date')} for lane in lanes] == [
        {'name': 'cloud', 'label': 'base', 'color': '#5b5b5b', 'repo_url': 'https://github.com/example/repo', 'ref': 'cloud', 'head': r['c6'], 'commits': 7},
        {
            'name': 'gcs', 'parent': 'cloud', 'color': '#4285f4', 'repo_url': 'https://github.com/example/repo',
            'aliases': ['gcs-old'], 'surfaces': {'intrinsic': ['own.txt']}, 'ref': 'gcs', 'head': r['merge'],
            'commits': 15, 'ahead': 8, 'behind': 0,
        },
    ]


def test_report_picks_and_merges(report):
    r, data = report['repo'], report['data']
    assert {sha: (p['lane'], p['from_lane'], p['class']) for sha, p in data['picks'].items()} == {
        r['c2']: ('cloud', 'gcs', 'equal@lines'),
        r['g5']: ('gcs', 'cloud', 'equal'),
        r['g6']: ('gcs', 'cloud', 'files-differ'),
        r['g7']: ('gcs', 'missing', 'missing'),
    }
    # equal@lines files carry no diff of diffs: the label says it all
    assert data['picks'][r['c2']]['files'] == [
        {'path': 'app.py', 'status': 'differs', 'level': 'lines', 'left_lines': 2, 'right_lines': 2, 'label': 'equal@lines'},
    ]
    [merge] = data['merges'].values()
    assert (merge['sha'], merge['lane'], merge['from_lane'], merge['class'], merge['ok']) == (r['merge'], 'gcs', 'cloud', 'equal@tokens', False)
    assert merge['counts'] == {'equal': 3, 'equal@tokens': 1, 'absorbed': 3, 'adopted': 1}
    assert sorted((f['path'], f['label']) for f in merge['files']) == [
        ('README', 'absorbed'),
        ('adopt.md', 'adopted'),
        ('app.py', 'absorbed'),
        ('sig.ts', 'equal@tokens'),
        ('x.txt', 'absorbed'),
    ]
    [sig] = [f for f in merge['files'] if f['path'] == 'sig.ts']
    assert sig['didi'] == [
        '@@ -3,6 +3,6 @@',
        ' --- a/sig.ts',
        ' +++ b/sig.ts',
        ' @@ -1,2 +1,2 @@',
        '--function f(a, b) {',
        '-+function f(a, b, staged = true) {',
        '+-function f(a, b, c) {',
        '++function f(a, b, c, staged = true) {',
        '  }',
    ]


def test_report_pairs(report):
    r, [pair] = report['repo'], report['data']['pairs']
    assert (pair['child'], pair['parent']) == ('gcs', 'cloud')
    shape = lambda row: tuple(row.get(k) for k in ('kind', 'sha', 'dir', 'via', 'parent_sha', 'class', 'surfaces'))
    assert [shape(row) for row in pair['rows']] == [
        ('pick', r['g1'], 'up', 'trailer', r['c2'], 'equal@lines', None),  # cloud picked g1 as c2
        ('own', r['g2'], None, None, None, None, ['other']),  # cloud copied adopt.md in a bigger commit: no patch-id match
        ('own', r['g3'], None, None, None, None, ['intrinsic']),
        ('own', r['g4'], None, None, None, None, ['other']),
        ('pick', r['g5'], 'down', 'trailer', r['c5'], 'equal', None),
        ('pick', r['g6'], 'down', 'trailer', r['c6'], 'files-differ', None),
        ('own', r['g7'], None, None, None, 'missing', ['other']),
        ('merge', r['merge'], None, None, None, 'equal@tokens', None),
    ]


def test_report_incremental(report, tmp_path):
    out2 = tmp_path / 'report2.json'
    result = invoke('report', '-c', str(report['cfg']), '-p', str(report['out']), '-o', str(out2))
    assert result.exit_code == 0
    assert result.stderr.split('\n')[:2] == ['picks: 4 reused, 0 to classify', 'merges: 1 reused, 0 to check']
    data2 = json.loads(out2.read_text())
    strip = lambda d: {k: v for k, v in d.items() if k != 'generated'}
    assert strip(data2) == strip(report['data'])


def test_report_incremental_options_mismatch(report, tmp_path):
    """Records built at another `-n` aren't reused."""
    result = invoke('report', '-c', str(report['cfg']), '-p', str(report['out']), '-n', 'tokens', '-o', str(tmp_path / 'r.json'))
    assert result.stderr.split('\n')[:2] == ['picks: 0 reused, 4 to classify', 'merges: 0 reused, 1 to check']


def test_load_cp_yml(tmp_path):
    cp = tmp_path / 'cp.yml'
    cp.write_text('branches:\n  cloud:\n    desc: the base\n  cw:\n    aliases: [cw-s3]\n')
    config = load_config(str(cp))
    assert [(lane.name, lane.parent, lane.label, lane.aliases) for lane in config.lanes] == [
        ('cloud', None, 'the base', []),
        ('cw', 'cloud', None, ['cw-s3']),
    ]


def test_report_bad_config(repo, tmp_path):
    cfg = tmp_path / 'didi.yml'
    cfg.write_text('lanes:\n  gcs: {parent: nope}\n')
    result = invoke('report', '-c', str(cfg))
    assert (result.exit_code, result.stderr) == (2, f"{cfg}: lane gcs's parent nope is not a lane\n")
