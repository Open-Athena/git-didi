"""`git-didi report`: one JSON bundle of lanes, first-parent segments, and audited picks/merges, for the web viewer."""

import re
from collections import Counter
from fnmatch import fnmatch
from difflib import unified_diff
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .compare import (
    DiffOpts,
    FileResult,
    Pick,
    classify_picks,
    display_lines,
    git,
    merge_delta,
    patch_ids,
    resolve_commits,
    since_arg,
    trailer_source,
)
from .normalize import DEFAULT_LEVEL

REPORT_VERSION = 2
MAX_FILE_DIDI_LINES = 200
MAX_ITEM_DIDI_LINES = 2000
# Files whose residual the viewer shows as a diff of diffs; the others' label (line numbers / context only, one-sided,
# absorbed, adopted) is the whole story.
DIDI_LABELS = ('differs', 'equal@ws', 'equal@tokens')
MERGE_RE = re.compile(r"^Merge (?:remote-tracking )?(?:branch|commit|tag) '([^']+)'(?: into (\S+))?")
SHA_RE = re.compile(r'[0-9a-f]{7,40}')
# A merge's class is its worst file's; `left-only`/`right-only` count as `differs`.
MERGE_CLASS_ORDER = ['equal', 'absorbed', 'adopted', 'equal@lines', 'equal@context', 'equal@ws', 'equal@tokens', 'differs']


@dataclass
class Lane:
    name: str
    parent: Optional[str] = None
    label: Optional[str] = None
    color: Optional[str] = None
    repo_url: Optional[str] = None
    aliases: list[str] = field(default_factory=list)
    surfaces: dict[str, list[str]] = field(default_factory=dict)
    ref: Optional[str] = None  # live branch: `name` if it exists, else the first existing alias

    def to_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], {})}


@dataclass
class Config:
    lanes: list[Lane]
    since: Optional[str] = None
    ignore_branches: list[str] = field(default_factory=list)  # globs never used to name a commit's branch


def load_config(path: str) -> Config:
    """Load `didi.yml` (`lanes: {name: {parent, label, color, repo_url, aliases, surfaces}}`, plus optional top-level
    `since`, `repo_url` (lanes' default), and `ignore_branches` globs).

    Also accepts a `/cp` skill `.claude/cp.yml` (`branches: {name: {aliases}}`): its first branch is the root, and the
    others are its children.
    """
    data = yaml.safe_load(Path(path).read_text()) or {}
    repo_url = data.get('repo_url')
    if 'lanes' in data:
        specs = data['lanes']
    elif 'branches' in data:
        names = list(data['branches'])
        specs = {
            name: {'aliases': (spec or {}).get('aliases', []), 'label': (spec or {}).get('desc'), 'parent': None if i == 0 else names[0]}
            for i, (name, spec) in enumerate(data['branches'].items())
        }
    else:
        raise ValueError(f"{path}: expected a `lanes:` (didi.yml) or `branches:` (cp.yml) mapping")
    lanes = []
    for name, spec in specs.items():
        spec = dict(spec or {})
        spec.setdefault('repo_url', repo_url)
        lanes.append(Lane(name=name, **spec))
    names = {lane.name for lane in lanes}
    for lane in lanes:
        if lane.parent is not None and lane.parent not in names:
            raise ValueError(f"{path}: lane {lane.name}'s parent {lane.parent} is not a lane")
    if not any(lane.parent is None for lane in lanes):
        raise ValueError(f"{path}: no root lane (one without a `parent`)")
    since = data.get('since')
    return Config(lanes, str(since) if since is not None else None, list(data.get('ignore_branches', [])))


@dataclass
class Meta:
    parents: list[str]
    date: str  # committer date, ISO 8601
    subject: str
    body: str


class Graph:
    """Commit metadata and lane membership, from a handful of `git` calls."""

    def __init__(self, lanes: list[Lane], ignore_branches: list[str] = ()):
        heads = dict(line.split(' ', 1)[::-1] for line in git('for-each-ref', '--format=%(objectname) %(refname:short)', 'refs/heads').splitlines())
        for lane in lanes:
            lane.ref = next((r for r in (lane.name, *lane.aliases) if r in heads), None)
            if lane.ref is None:
                raise ValueError(f"lane {lane.name}: no branch named {lane.name} or any of its aliases {lane.aliases}")
        self.lanes = {lane.name: lane for lane in lanes}
        self.ignore_branches = list(ignore_branches)
        self.heads = heads
        self.canon = {alias: lane.name for lane in lanes for alias in (lane.name, *lane.aliases)}
        self.meta: dict[str, Meta] = {}
        self.load_meta(*sorted({heads[lane.ref] for lane in lanes}))
        self.reach = {name: set(rev_list(self.tip(name))) for name in self.lanes}
        self.own = {
            name: set(rev_list(self.tip(name), f'^{self.tip(lane.parent)}')) if lane.parent else self.reach[name]
            for name, lane in self.lanes.items()
        }
        self.spines = {
            name: set(rev_list('--first-parent', self.tip(name), f'^{self.tip(lane.parent)}'))
            for name, lane in self.lanes.items() if lane.parent
        }
        # Attribute a commit to the deepest lane whose own set has it (a lane's own set excludes its parent's commits)
        self.by_depth = sorted((n for n, lane in self.lanes.items() if lane.parent), key=self.depth, reverse=True)
        self._containing: dict[str, str] = {}
        self.resolved: dict[str, Optional[str]] = {}  # pick trailer SHA → full SHA (None: missing)

    def depth(self, name: str) -> int:
        parent = self.lanes[name].parent
        return 0 if parent is None else 1 + self.depth(parent)

    def tip(self, name: str) -> str:
        return self.heads[self.lanes[name].ref]

    def roots(self) -> list[str]:
        return [n for n, lane in self.lanes.items() if lane.parent is None]

    def load_meta(self, *revs: str, no_walk: bool = False) -> None:
        if not revs:
            return
        args = ['log', '--format=%H%x1f%P%x1f%cI%x1f%s%x1f%b%x1e']
        if no_walk:
            args.append('--no-walk=unsorted')
        out = git(*args, *revs, '--')
        for rec in out.split('\x1e'):
            rec = rec.strip('\n')
            if not rec:
                continue
            sha, parents, date, subject, body = rec.split('\x1f')
            self.meta[sha] = Meta(parents.split(), date, subject, body)

    def home(self, sha: str) -> Optional[str]:
        """The lane a commit belongs to: a root that reaches it, else a lane whose spine or own set has it."""
        for root in self.roots():
            if sha in self.reach[root]:
                return root
        for name in self.by_depth:
            if sha in self.spines[name]:
                return name
        for name in self.by_depth:
            if sha in self.own[name]:
                return name
        return None

    def containing_ref(self, sha: str) -> str:
        """A local branch containing `sha` that isn't a lane (e.g. the merged feature branch), else ''."""
        if sha not in self._containing:
            names = git('for-each-ref', '--contains', sha, '--format=%(refname:short)', 'refs/heads').split()
            self._containing[sha] = next(
                (n for n in names if n not in self.canon and not any(fnmatch(n, g) for g in self.ignore_branches)),
                '',
            )
        return self._containing[sha]

    def merge_source(self, sha: str, me: str) -> tuple[str, str]:
        """(source, kind) of a merge's second parent; kind ∈ parent | peer | feature."""
        md = self.meta[sha]
        p2 = md.parents[1]
        parent = self.lanes[me].parent
        if parent and p2 in self.reach[parent]:
            return parent, 'parent'
        home = self.home(p2)
        if home and home != me and home not in self.roots():
            return home, 'peer'
        m = MERGE_RE.match(md.subject)
        name = self.canon_name(m.group(1)) if m else ''
        if m and name == me and m.group(2):
            # "Merge branch '<me>' into <feature>", then fast-forwarded into <me>
            name = self.canon_name(m.group(2))
        if SHA_RE.fullmatch(name):
            name = self.canon.get(self.containing_ref(p2), self.containing_ref(p2))
        if name in self.lanes and name not in (parent, me):
            return name, 'peer'
        if not name or name == me or name in self.lanes:
            name = self.containing_ref(p2) or '(unnamed feature branch)'
        return name, 'feature'

    def canon_name(self, ref: str) -> str:
        ref = ref.removeprefix('refs/heads/')
        return self.canon.get(ref, ref)


def rev_list(*revs: str) -> list[str]:
    return git('rev-list', *revs, '--').split()


def spine(graph: Graph, name: str, since: Optional[str]) -> list[str]:
    lane = graph.lanes[name]
    revs = ['--first-parent', '--reverse', graph.tip(name)]
    if lane.parent:
        revs.append(f'^{graph.tip(lane.parent)}')
    if since:
        revs.insert(0, since_arg(since))
    return rev_list(*revs)


def commit_rec(graph: Graph, sha: str) -> dict:
    md = graph.meta[sha]
    rec = {'sha': sha, 'date': md.date, 'subject': md.subject}
    if len(md.parents) > 1:
        rec['merge'] = True
    return rec


def segments(graph: Graph, name: str, commits: list[str]) -> list[dict]:
    """A lane's first-parent history as runs of own commits, parent/peer merges, and pick groups (by source lane)."""
    segs: list[dict] = []
    run: Optional[dict] = None

    def flush():
        nonlocal run
        if run:
            cs = run.pop('_commits')
            run.update(
                n=len(cs),
                first_date=graph.meta[cs[0]].date,
                last_date=graph.meta[cs[-1]].date,
                commits=[commit_rec(graph, c) for c in cs],
                feature_merges=dict(Counter(run['feature_merges']).most_common()),
            )
            segs.append(run)
            run = None

    def run_add(sha: str) -> dict:
        nonlocal run
        run = run or {'kind': 'run', '_commits': [], 'feature_merges': [], 'side_commits': 0}
        run['_commits'].append(sha)
        return run

    for sha in commits:
        md = graph.meta[sha]
        if len(md.parents) > 1:
            src, kind = graph.merge_source(sha, name)
            n_in = len(rev_list(md.parents[1], f'^{md.parents[0]}'))
            if kind in ('parent', 'peer'):
                flush()
                segs.append({'kind': 'merge', 'sha': sha, 'date': md.date, 'from_lane': src, 'from_kind': kind, 'subject': md.subject, 'n_in': n_in})
            else:
                r = run_add(sha)
                r['feature_merges'].append(src)
                r['side_commits'] += n_in
            continue
        src = trailer_source(md.body)
        if src:
            flush()
            last = segs[-1] if segs else None
            item = {'sha': sha, 'date': md.date, 'source': src, 'subject': md.subject}
            from_lane = pick_source_lane(graph, src)
            if last and last['kind'] == 'cp' and last['from_lane'] == from_lane:
                last['picks'].append(item)
                last['n'] += 1
                last['last_date'] = md.date
            else:
                segs.append({'kind': 'cp', 'date': md.date, 'last_date': md.date, 'from_lane': from_lane, 'n': 1, 'picks': [item]})
            continue
        run_add(sha)
    flush()
    return segs


def pick_source_lane(graph: Graph, source: str) -> str:
    """The lane (or other branch) a pick's source belongs to; `missing` if it's gone from the repo."""
    full = graph.resolved.get(source)
    if full is None:
        return 'missing'
    return graph.home(full) or graph.containing_ref(full) or 'dangling'


def file_recs(
    files: list[FileResult],
    labels: tuple[str, ...] = None,
    file_lines: int = None,
    item_lines: int = None,
) -> list[dict]:
    """Records of the non-equal files; those in `labels` (default `DIDI_LABELS`) carry their diff of diffs, so the viewer
    needs no git.

    `didi` is the outer unified diff of the two `index`-normalized patches (no `---`/`+++` header). It's capped at
    `file_lines` per file and `item_lines` per pick/merge (defaults: `MAX_FILE_DIDI_LINES`, `MAX_ITEM_DIDI_LINES`);
    `didi_lines` holds the full count when
    a file's `didi` is truncated (or omitted, once the item's budget is spent).
    """
    labels = DIDI_LABELS if labels is None else labels
    file_lines = MAX_FILE_DIDI_LINES if file_lines is None else file_lines
    recs = []
    budget = MAX_ITEM_DIDI_LINES if item_lines is None else item_lines
    for f in files:
        if f.status == 'equal':
            continue
        rec = f.to_json()
        rec['label'] = f.label
        if rec['label'] in labels:
            left, right = display_lines(f, 'index')
            didi = list(unified_diff(left, right, lineterm=''))[2:]
            keep = min(len(didi), file_lines, budget)
            if keep < len(didi):
                rec['didi_lines'] = len(didi)
            rec['didi'] = didi[:keep]
            budget -= keep
        recs.append(rec)
    return recs


def merge_class(files: list[FileResult]) -> str:
    labels = ['differs' if f.label in ('left-only', 'right-only') else f.label for f in files]
    return max(labels, key=MERGE_CLASS_ORDER.index, default='equal')


def commit_paths(shas: list[str]) -> dict[str, list[str]]:
    """Paths each commit touches (vs. its first parent), from one `git log` call."""
    if not shas:
        return {}
    out = git('log', '--no-walk=unsorted', '--stdin', '--diff-merges=first-parent', '--name-only', '--format=%x00%H', input='\n'.join(shas) + '\n')
    paths = {}
    for chunk in out.split('\0')[1:]:
        sha, _, body = chunk.partition('\n')
        paths[sha] = [p for p in body.splitlines() if p]
    return paths


def surface_match(path: str, pattern: str) -> bool:
    """A glob, or (with no glob characters) a path prefix: `site/src` matches `site/src/App.tsx`."""
    if any(c in pattern for c in '*?['):
        return fnmatch(path, pattern)
    return path == pattern or path.startswith(pattern.rstrip('/') + '/')


def surfaces_of(paths: list[str], surfaces: dict[str, list[str]]) -> list[str]:
    """Names of the surfaces any of `paths` falls in (config order); `other` when some path falls in none."""
    hit = [name for name, pats in surfaces.items() if any(surface_match(p, pat) for p in paths for pat in pats)]
    if not surfaces or any(not any(surface_match(p, pat) for pats in surfaces.values() for pat in pats) for p in paths):
        hit.append('other')
    return hit


def pair_tracks(
    graph: Graph,
    segs: dict[str, list[dict]],
    picks: dict[str, dict],
    spines: dict[str, list[str]],
    opts: DiffOpts,
    threshold: str,
) -> list[dict]:
    """Per (child, parent) lane pair: the child's first-parent commits as rows, each pick aligned with its source.

    Rows (oldest first): `pick` (a pick from the parent, `dir: down`, or a child commit the parent picked, `dir: up`;
    `via` a `-x` trailer, else equal `git patch-id --stable`), `merge` (a merge from the parent or a peer), and `own`
    (unpaired: the child's intrinsic delta, tagged with the configured surfaces its paths touch).
    """
    out = []
    for child, lane in graph.lanes.items():
        parent = lane.parent
        if not parent:
            continue
        rows: list[dict] = []
        for s in segs[child]:
            if s['kind'] == 'run':
                for c in s['commits']:
                    rows.append({'kind': 'own', 'sha': c['sha'], 'date': c['date'], 'subject': c['subject'], **({'merge': True} if c.get('merge') else {})})
            elif s['kind'] == 'cp':
                for item in s['picks']:
                    if s['from_lane'] == parent:
                        rows.append({'kind': 'pick', 'dir': 'down', 'via': 'trailer', 'sha': item['sha'], 'date': item['date'], 'subject': item['subject'], 'parent_sha': picks[item['sha']]['source'], 'class': item['class']})
                    else:
                        rows.append({'kind': 'own', 'sha': item['sha'], 'date': item['date'], 'subject': item['subject'], 'from_lane': s['from_lane'], 'class': item['class']})
            else:
                rows.append({k: s[k] for k in ('kind', 'sha', 'date', 'subject', 'from_lane', 'from_kind', 'n_in') if k in s} | ({'class': s['class']} if 'class' in s else {}))
        by_sha = {r['sha']: r for r in rows}

        # Up: parent commits picked from this child (by trailer)
        for s in segs[parent]:
            if s['kind'] != 'cp' or s['from_lane'] != child:
                continue
            for item in s['picks']:
                src = picks[item['sha']]['source']
                r = by_sha.get(src)
                if r and r['kind'] == 'own' and not r.get('merge'):
                    r.update(kind='pick', dir='up', via='trailer', parent_sha=item['sha'], **{'class': item['class']})

        # Patch-id: unpaired own commits equal to an (unpaired) parent spine commit
        own = [r['sha'] for r in rows if r['kind'] == 'own' and not r.get('merge') and 'from_lane' not in r]
        paired = {r['parent_sha'] for r in rows if r['kind'] == 'pick'}
        parent_commits = [c for c in spines[parent] if len(graph.meta[c].parents) == 1 and c not in paired]
        if own and parent_commits:
            own_pids = patch_ids(['--no-walk=unsorted', *own], opts)
            parent_by_pid: dict[str, str] = {}
            for c, pid in patch_ids(['--no-walk=unsorted', *parent_commits], opts).items():
                parent_by_pid.setdefault(pid, c)
            matches = [(sha, parent_by_pid[pid]) for sha, pid in own_pids.items() if pid in parent_by_pid]
            for r in classify_picks([Pick(sha, '', p, p) for sha, p in matches], opts, threshold):
                row = by_sha[r.pick.commit]
                # Direction: whichever side committed first is the source
                up = graph.meta[row['sha']].date <= graph.meta[r.pick.source].date
                row.update(kind='pick', dir='up' if up else 'down', via='patch-id', parent_sha=r.pick.source, **{'class': r.cls})
                files = file_recs(r.files)
                if files:
                    row['files'] = files

        # Surfaces of the intrinsic delta
        if lane.surfaces:
            own_rows = [r for r in rows if r['kind'] == 'own']
            paths = commit_paths([r['sha'] for r in own_rows])
            for r in own_rows:
                ps = paths.get(r['sha'], [])
                r['surfaces'] = surfaces_of(ps, lane.surfaces)
                r['n_paths'] = len(ps)
        out.append({'child': child, 'parent': parent, 'rows': rows})
    return out


def options_key(opts: DiffOpts, threshold: str) -> dict:
    return {
        'normalize': threshold,
        'unified': opts.unified,
        'ignore_whitespace': opts.ignore_whitespace,
        'find_renames': opts.find_renames,
        'find_copies': opts.find_copies,
    }


def build_report(
    config: Config,
    since: Optional[str] = None,
    opts: DiffOpts = DiffOpts(),
    threshold: str = DEFAULT_LEVEL,
    previous: Optional[dict] = None,
    jobs: int = 8,
    log=lambda msg: None,
) -> dict:
    since = since or config.since
    options = options_key(opts, threshold)
    reuse = previous if previous and previous.get('version') == REPORT_VERSION and previous.get('options') == options else None
    graph = Graph(config.lanes, config.ignore_branches)

    spines = {name: spine(graph, name, since) for name in graph.lanes}
    sources = sorted({
        src for commits in spines.values() for sha in commits
        if len(graph.meta[sha].parents) == 1 and (src := trailer_source(graph.meta[sha].body))
    })
    graph.resolved = resolve_commits(sources)
    graph.load_meta(*sorted({s for s in graph.resolved.values() if s and s not in graph.meta}), no_walk=True)
    segs = {name: segments(graph, name, commits) for name, commits in spines.items()}

    # Picks: every pick on a lane's spine
    lane_picks = [(name, item) for name, ss in segs.items() for s in ss if s['kind'] == 'cp' for item in s['picks']]
    old_picks = (reuse or {}).get('picks', {})
    picks, todo = {}, []
    for name, item in lane_picks:
        old = old_picks.get(item['sha'])
        full = graph.resolved[item['source']]
        if old and old['source'] == (full or item['source']):
            picks[item['sha']] = old
        else:
            todo.append((name, Pick(item['sha'], item['subject'], item['source'], full)))
    log(f"picks: {len(picks)} reused, {len(todo)} to classify")
    lanes_of = {p.commit: name for name, p in todo}
    for r in classify_picks([p for _, p in todo], opts, threshold):
        rec = r.to_json(threshold)
        rec['files'] = file_recs(r.files)
        rec['lane'] = lanes_of[r.pick.commit]
        rec['from_lane'] = pick_source_lane(graph, r.pick.source)
        rec['date'] = graph.meta[r.pick.commit].date
        picks[r.pick.commit] = rec

    # Merges: own-delta check of every parent merge
    parent_merges = [(name, s) for name, ss in segs.items() for s in ss if s['kind'] == 'merge' and s['from_kind'] == 'parent']
    old_merges = (reuse or {}).get('merges', {})
    merges = {s['sha']: old_merges[s['sha']] for _, s in parent_merges if s['sha'] in old_merges}
    todo_merges = [(name, s) for name, s in parent_merges if s['sha'] not in merges]
    log(f"merges: {len(merges)} reused, {len(todo_merges)} to check")

    def check(item: tuple[str, dict]) -> dict:
        name, s = item
        result = merge_delta(s['sha'], opts, threshold)
        rec = result.to_json()
        rec.pop('label')
        rec['counts'] = dict(Counter(f.label for f in result.files))
        rec['files'] = file_recs(result.files)
        rec.update(sha=s['sha'], lane=name, from_lane=s['from_lane'], date=s['date'], subject=s['subject'], **{'class': merge_class(result.files)})
        return rec

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for rec in pool.map(check, todo_merges):
            merges[rec['sha']] = rec

    for ss in segs.values():
        for s in ss:
            if s['kind'] == 'merge' and s['sha'] in merges:
                s['class'] = merges[s['sha']]['class']
            elif s['kind'] == 'cp':
                for item in s['picks']:
                    item['class'] = picks[item['sha']]['class']

    pairs = pair_tracks(graph, segs, picks, spines, opts, threshold)
    log(f"pairs: {len(pairs)} lane pair(s)")

    lanes = []
    for name, lane in graph.lanes.items():
        rec = lane.to_json()
        tip = graph.tip(name)
        rec.update(head=tip, head_date=graph.meta[tip].date, commits=len(graph.reach[name]))
        if lane.parent:
            own = graph.own[name]
            rec.update(
                ahead=len(own),
                behind=len(rev_list(graph.tip(lane.parent), f'^{tip}')),
                fork_date=min(graph.meta[c].date for c in own) if own else None,
            )
        lanes.append(rec)

    return {
        'version': REPORT_VERSION,
        'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'since': since,
        'options': options,
        'lanes': lanes,
        'segments': segs,
        'picks': picks,
        'merges': merges,
        'pairs': pairs,
    }

