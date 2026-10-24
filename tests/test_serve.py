"""Tests for `git-didi serve`'s HTTP API, on the `conftest.repo` fixture."""

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from didi.compare import DiffOpts
from didi.report import build_report, load_config
from didi.serve import ReportCache, make_handler


@pytest.fixture
def server(repo, tmp_path):
    www = tmp_path / 'www'
    www.mkdir()
    (www / 'index.html').write_text('<!doctype html><title>viewer</title>\n')
    cfg = tmp_path / 'didi.yml'
    cfg.write_text('lanes:\n  cloud: {}\n  gcs: {parent: cloud}\n')
    config = load_config(str(cfg))
    logs: list[str] = []
    builds: list[bool] = []

    def build(prev):
        builds.append(prev is not None)
        return build_report(config, previous=prev)

    cache = ReportCache(config, build, logs.append)
    info = {'serve': True, 'repo': 'repo', 'repo_url': None, 'report': True}
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(www, cache, DiffOpts(), 'index', info, logs.append))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {'url': f'http://127.0.0.1:{httpd.server_address[1]}', 'repo': repo, 'builds': builds, 'cache': cache}
    httpd.shutdown()


def get(url: str) -> tuple[int, str, bytes]:
    try:
        with urlopen(url) as res:
            return res.status, res.headers['Content-Type'], res.read()
    except HTTPError as e:
        return e.code, e.headers['Content-Type'], e.read()


def test_info_and_static(server):
    assert get(f"{server['url']}/api/info") == (200, 'application/json', b'{"serve":true,"repo":"repo","repo_url":null,"report":true}')
    # unknown paths fall back to the viewer (it routes by query params)
    for path in ('/', '/index.html', '/some/deep/path'):
        assert get(f"{server['url']}{path}") == (200, 'text/html', b'<!doctype html><title>viewer</title>\n')


def test_patch(server):
    r = server['repo']
    status, _, body = get(f"{server['url']}/api/patch?left={r['g4']}^..{r['g4']}&right={r['c4']}^..{r['c4']}")
    data = json.loads(body)
    assert status == 200
    assert (data['left'], data['right'], data['counts']) == (
        {'spec': f"{r['g4']}^..{r['g4']}", 'base': r['g3'], 'tip': r['g4']},
        {'spec': f"{r['c4']}^..{r['c4']}", 'base': r['c3'], 'tip': r['c4']},
        {'differs': 1},
    )
    [f] = data['files']
    assert (f['path'], f['label'], f['didi'][-3:]) == ('sig.ts', 'differs', ['-+function f(a, b, staged = true) {', '++function f(a, b, c) {', '  }'])


def test_patch_levels_and_errors(server):
    r = server['repo']
    url = f"{server['url']}/api/patch?left={r['g1']}^..{r['g1']}&right={r['c2']}^..{r['c2']}"
    assert json.loads(get(url)[2])['counts'] == {'equal@lines': 1}
    # serve shows every non-equal file's diff of diffs, even ones a report would summarize by label
    assert json.loads(get(url)[2])['files'][0]['didi'][0].startswith('@@')
    assert get(f'{url}&n=nope') == (400, 'application/json', b'{"error":"Unknown level nope"}')
    assert get(f"{server['url']}/api/patch?left=x") == (400, 'application/json', b'{"error":"Expected ?left=A..B&right=C..D"}')
    status, _, body = get(f"{server['url']}/api/patch?left=nope..gcs&right=gcs~1..gcs")
    assert (status, json.loads(body)) == (400, {'error': "fatal: ambiguous argument 'nope..gcs': unknown revision or path not in the working tree."})


def test_report_cached_and_refreshed(server):
    status, _, body = get(f"{server['url']}/api/report")
    data = json.loads(body)
    assert (status, sorted(data['picks']), server['builds']) == (200, sorted([server['repo'][k] for k in ('c2', 'g5', 'g6', 'g7')]), [False])
    assert json.loads(Path(server['cache'].path).read_text()) == data
    get(f"{server['url']}/api/report")
    assert server['builds'] == [False]  # served from memory
    get(f"{server['url']}/api/report?refresh")
    assert server['builds'] == [False, True]  # incremental rebuild from the previous report
