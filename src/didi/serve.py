"""`git-didi serve`: the web viewer, against the current repo, with on-demand reports and arbitrary range comparisons."""

import json
import mimetypes
import re
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from .compare import DiffOpts, GitError, compare_patches, git, range_patches, rev_parse
from .diff import compute_upstream_range, get_rename_mapping
from .normalize import LEVELS
from .report import Config, build_report, file_recs

# Arbitrary comparisons show every non-equal file's diff of diffs, with higher caps than a report's
LABELS_ALL = ('differs', 'equal@ws', 'equal@tokens', 'equal@context', 'equal@lines', 'left-only', 'right-only')
FILE_LINES, ITEM_LINES = 2000, 20000


def find_www(explicit: Optional[str] = None) -> Path:
    """The built viewer: `--www`, else the packaged `didi/www_dist`, else (editable install) the repo's `www/dist`."""
    here = Path(__file__).resolve().parent
    for cand in ([Path(explicit)] if explicit else []) + [here / 'www_dist', here.parent.parent / 'www' / 'dist']:
        if (cand / 'index.html').is_file():
            return cand
    raise FileNotFoundError("No built viewer found (pass --www, or run `pnpm build` in www/)")


def github_url() -> Optional[str]:
    """`https://github.com/<org>/<repo>` from the first GitHub remote, if any."""
    for line in git('remote', '-v').splitlines():
        m = re.search(r'github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s', line + ' ')
        if m:
            return f'https://github.com/{m.group(1)}'
    return None


def range_endpoints(spec: str) -> tuple[str, str]:
    """Resolved (base, tip) SHAs of `A..B` (or a single commit `C`: `C^..C`)."""
    base, tip = spec.split('..', 1) if '..' in spec else (f'{spec}^', spec)
    return rev_parse(base), rev_parse(tip)


def compare_ranges(left: str, right: str, opts: DiffOpts, threshold: str) -> dict:
    """Like `git-didi patch LEFT RIGHT --json`, plus every non-equal file's diff of diffs, for the viewer."""
    upstream = compute_upstream_range(left, right)
    rename_map = get_rename_mapping(upstream, opts.find_renames, opts.find_copies) if upstream else {}
    results = compare_patches(range_patches(left, opts=opts), range_patches(right, opts=opts), rename_map, threshold)
    (lb, lt), (rb, rt) = range_endpoints(left), range_endpoints(right)
    return {
        'left': {'spec': left, 'base': lb, 'tip': lt},
        'right': {'spec': right, 'base': rb, 'tip': rt},
        'options': {'normalize': threshold},
        'counts': dict(Counter(r.label for r in results)),
        'files': file_recs(results, labels=LABELS_ALL, file_lines=FILE_LINES, item_lines=ITEM_LINES),
    }


class ReportCache:
    """One report per server, built on first request and kept at `$GIT_DIR/didi/report.json` (rebuilds are incremental)."""

    def __init__(self, config: Optional[Config], build: Callable[[Optional[dict]], dict], log: Callable[[str], None]):
        self.config = config
        self.build = build
        self.log = log
        self.lock = threading.Lock()
        self.path = Path(git('rev-parse', '--absolute-git-dir').strip()) / 'didi' / 'report.json'
        self.data: Optional[dict] = None

    def get(self, refresh: bool = False) -> dict:
        with self.lock:
            if self.data is None and self.path.is_file():
                self.data = json.loads(self.path.read_text())
            if self.data is None or refresh:
                self.log(f"Building report{' (incremental)' if self.data else ''}…")
                self.data = self.build(self.data)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data, separators=(',', ':')) + '\n')
                self.log(f"Report: {len(self.data['picks'])} picks, {len(self.data['merges'])} merges → {self.path}")
            return self.data


def make_handler(www: Path, cache: Optional[ReportCache], opts: DiffOpts, threshold: str, info: dict, log: Callable[[str], None]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log(f"{self.address_string()} {fmt % args}")

        def send_json(self, data, status: int = 200):
            body = json.dumps(data, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            try:
                if url.path == '/api/info':
                    return self.send_json(info)
                if url.path == '/api/report':
                    if cache is None:
                        return self.send_json({'error': 'No lanes config (pass -c, or add didi.yml)'}, 404)
                    return self.send_json(cache.get(refresh='refresh' in q))
                if url.path == '/api/patch':
                    if 'left' not in q or 'right' not in q:
                        return self.send_json({'error': 'Expected ?left=A..B&right=C..D'}, 400)
                    level = q.get('n', threshold)
                    if level not in LEVELS:
                        return self.send_json({'error': f'Unknown level {level}'}, 400)
                    return self.send_json(compare_ranges(q['left'], q['right'], opts, level))
            except GitError as e:
                # e.g. an unknown ref
                return self.send_json({'error': e.stderr.splitlines()[0] if e.stderr else f'git {e.cmd[0]} failed'}, 400)
            return self.serve_static(url.path)

        def serve_static(self, path: str):
            rel = path.lstrip('/') or 'index.html'
            file = (www / rel).resolve()
            if not file.is_relative_to(www.resolve()) or not file.is_file():
                file = www / 'index.html'  # SPA: unknown paths render the viewer
            body = file.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(
    host: str,
    port: int,
    www: Path,
    config: Optional[Config],
    opts: DiffOpts,
    threshold: str,
    since: Optional[str],
    jobs: int,
    log: Callable[[str], None],
) -> None:
    cache = None
    if config is not None:
        cache = ReportCache(config, lambda prev: build_report(config, since, opts, threshold, prev, jobs, log=log), log)
    top = Path(git('rev-parse', '--show-toplevel').strip())
    info = {'serve': True, 'repo': top.name, 'repo_url': github_url(), 'report': config is not None}
    server = ThreadingHTTPServer((host, port), make_handler(www, cache, opts, threshold, info, log))
    log(f"git-didi viewer for {top} at http://{host}:{port}/ (viewer: {www})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
