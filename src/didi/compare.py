"""Batched patch fetching, per-file classification, pick fidelity, and merge/delta invariance."""

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from subprocess import run
from typing import Optional

from utz import err

from .normalize import (
    DEFAULT_LEVEL,
    changed_lines,
    first_equal_level,
    level_idx,
    level_label,
    line_counts,
    normalize,
    split_patch,
)

# Pin output format against user config (`diff.noprefix`, `diff.mnemonicPrefix`, `color.diff`, external diff drivers).
GIT = ['git', '-c', 'core.quotePath=false']
DIFF_FLAGS = ['--no-color', '--no-ext-diff', '--src-prefix=a/', '--dst-prefix=b/']
PICK_RE = re.compile(r'^\(cherry picked from commit ([0-9a-f]{4,40})\)$', re.M)


@dataclass
class DiffOpts:
    ignore_whitespace: bool = False
    unified: int = 3
    find_renames: Optional[str] = None
    find_copies: Optional[str] = None

    def flags(self) -> list[str]:
        flags = [*DIFF_FLAGS, f'-U{self.unified}']
        if self.ignore_whitespace:
            flags.append('-w')
        if self.find_renames:
            flags.append(f'-M{self.find_renames}')
        if self.find_copies:
            flags.append(f'-C{self.find_copies}')
        return flags


class GitError(SystemExit):
    """A failed `git` command: exits 2 from the CLI; `git-didi serve` reports `stderr` instead."""

    def __init__(self, args: tuple[str, ...], stderr: str):
        super().__init__(2)
        self.cmd = args
        self.stderr = stderr


def git(*args: str, input: Optional[str] = None) -> str:
    result = run([*GIT, *args], capture_output=True, text=True, input=input)
    if result.returncode != 0:
        err(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        raise GitError(args, result.stderr.strip())
    return result.stdout


def rev_parse(rev: str) -> str:
    return git('rev-parse', '--verify', '--quiet', f'{rev}^{{commit}}').strip()


def merge_base(a: str, b: str) -> str:
    return git('merge-base', a, b).strip()


def range_patches(
    *revs: str,
    opts: DiffOpts = DiffOpts(),
    paths: tuple[str, ...] = (),
) -> dict[str, str]:
    """Per-file patches of one `git diff` call (e.g. `range_patches('A..B')` or `range_patches(A, B)`)."""
    cmd = ['diff', *opts.flags(), *revs]
    if paths:
        cmd.extend(['--', *paths])
    return split_patch(git(*cmd))


def commit_patches(shas: list[str], opts: DiffOpts = DiffOpts()) -> dict[str, dict[str, str]]:
    """Per-file patches of each commit (vs. its first parent), from one `git log` call."""
    if not shas:
        return {}
    out = git(
        'log', '--no-walk=unsorted', '--stdin', '--diff-merges=first-parent', '-p',
        '--format=%x00%H', *opts.flags(),
        input='\n'.join(shas) + '\n',
    )
    patches = {}
    for chunk in out.split('\0')[1:]:
        sha, _, body = chunk.partition('\n')
        patches[sha] = split_patch(body)
    for sha in shas:
        patches.setdefault(sha, {})
    return patches


@dataclass
class FileResult:
    path: str
    left_path: str
    status: str  # equal | differs | left-only | right-only | absorbed | adopted
    level: Optional[str]  # least-lenient normalization level at which both sides are equal
    left: Optional[str] = field(default=None, repr=False)
    right: Optional[str] = field(default=None, repr=False)
    residual_lines: int = 0
    absorbed_lines: int = 0

    @property
    def display_name(self) -> str:
        return f'{self.left_path} → {self.path}' if self.left_path != self.path else self.path

    @property
    def label(self) -> str:
        if self.status in ('left-only', 'right-only', 'absorbed', 'adopted'):
            return self.status
        return level_label(self.level)

    def to_json(self) -> dict:
        left_add, left_del = line_counts(self.left)
        right_add, right_del = line_counts(self.right)
        rec = {'path': self.path}
        if self.left_path != self.path:
            rec['left_path'] = self.left_path
        rec.update(
            status=self.status,
            level=self.level,
            left_lines=left_add + left_del,
            right_lines=right_add + right_del,
        )
        if self.residual_lines:
            rec.update(residual_lines=self.residual_lines, absorbed_lines=self.absorbed_lines)
        return rec


def compare_patches(
    left: dict[str, str],
    right: dict[str, str],
    rename_map: Optional[dict[str, str]] = None,
    threshold: str = DEFAULT_LEVEL,
) -> list[FileResult]:
    """Pair up per-file patches (applying `rename_map` to left paths) and classify each pair."""
    rename_map = rename_map or {}
    results = []
    seen = set()
    for left_path, left_patch in left.items():
        path = rename_map.get(left_path, left_path)
        seen.add(path)
        right_patch = right.get(path)
        if right_patch is None:
            results.append(FileResult(path, left_path, 'left-only', None, left=left_patch))
            continue
        level = first_equal_level(left_patch, right_patch, rename_map)
        status = 'equal' if level is not None and level_idx(level) <= level_idx(threshold) else 'differs'
        results.append(FileResult(path, left_path, status, level, left=left_patch, right=right_patch))
    for path, right_patch in right.items():
        if path not in seen:
            results.append(FileResult(path, path, 'right-only', None, right=right_patch))
    return results


SUMMARY_ORDER = ['equal', 'equal@lines', 'equal@context', 'equal@ws', 'equal@tokens', 'absorbed', 'adopted', 'differs', 'left-only', 'right-only']


def summarize(labels: list[str], order: list[str] = SUMMARY_ORDER) -> str:
    counts = Counter(labels)
    keys = [k for k in order if k in counts] + sorted(k for k in counts if k not in order)
    return ', '.join(f'{counts[k]} {k}' for k in keys)


def display_lines(result: FileResult, threshold: str) -> tuple[list[str], list[str]]:
    """Both sides' patch lines, normalized for display (index noise stripped unless `-n none`)."""
    level = 'none' if threshold == 'none' else 'index'
    left = normalize(result.left, level) if result.left is not None else []
    right = normalize(result.right, level) if result.right is not None else []
    return left, right


# Pick fidelity

@dataclass
class Pick:
    commit: str
    subject: str
    source: str  # as written in the trailer
    source_sha: Optional[str]  # resolved; None if missing from the repo


@dataclass
class PickResult:
    pick: Pick
    cls: str  # equal | equal@<level> | differs | files-differ | missing
    level: Optional[str]
    files: list[FileResult]

    def ok(self, threshold: str) -> bool:
        if self.cls == 'missing':
            return True
        return self.level is not None and level_idx(self.level) <= level_idx(threshold)

    def to_json(self, threshold: str) -> dict:
        p = self.pick
        return {
            'commit': p.commit,
            'subject': p.subject,
            'source': p.source_sha or p.source,
            'class': self.cls,
            'level': self.level,
            'ok': self.ok(threshold),
            'files': [f.to_json() for f in self.files if f.status != 'equal'],
        }


def resolve_commits(revs: list[str]) -> dict[str, Optional[str]]:
    """Resolve each rev to a full commit SHA (None if missing or ambiguous), in one `cat-file` call."""
    if not revs:
        return {}
    out = git('cat-file', '--batch-check=%(objectname) %(objecttype)', input=''.join(f'{r}^{{commit}}\n' for r in revs))
    resolved = {}
    for rev, line in zip(revs, out.splitlines()):
        parts = line.split()
        resolved[rev] = parts[0] if len(parts) == 2 and parts[1] == 'commit' else None
    return resolved


def trailer_source(body: str) -> Optional[str]:
    """Immediate `-x` source of a commit (the last trailer, for a pick of a pick)."""
    sources = PICK_RE.findall(body)
    return sources[-1] if sources else None


DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def since_arg(since: str) -> str:
    """`--since=…`, pinning a bare `YYYY-MM-DD` to midnight (git's approxidate otherwise reads it as that date at the
    current time of day, silently dropping that day's earlier commits)."""
    return f'--since={since} 00:00:00' if DATE_RE.match(since) else f'--since={since}'


def log_bodies(*revs: str, since: Optional[str] = None, grep: Optional[str] = None) -> list[tuple[str, str, str]]:
    """(sha, subject, body) for each commit in `revs`."""
    cmd = ['log', '--format=%H%x00%s%x00%B%x01']
    if since:
        cmd.append(since_arg(since))
    if grep:
        cmd.extend(['-F', f'--grep={grep}'])
    out = git(*cmd, *revs, '--')
    commits = []
    for rec in out.split('\x01'):
        rec = rec.lstrip('\n')
        if not rec:
            continue
        sha, subject, body = rec.split('\0', 2)
        commits.append((sha, subject, body))
    return commits


def find_picks(revs: tuple[str, ...], since: Optional[str] = None) -> list[Pick]:
    commits = [
        (sha, subject, src)
        for sha, subject, body in log_bodies(*revs, since=since, grep='(cherry picked from commit ')
        if (src := trailer_source(body))
    ]
    resolved = resolve_commits(sorted({src for _, _, src in commits}))
    return [Pick(sha, subject, src, resolved[src]) for sha, subject, src in commits]


def classify_picks(
    picks: list[Pick],
    opts: DiffOpts = DiffOpts(),
    threshold: str = DEFAULT_LEVEL,
) -> list[PickResult]:
    shas = list(dict.fromkeys([p.commit for p in picks] + [p.source_sha for p in picks if p.source_sha]))
    patches = commit_patches(shas, opts)
    results = []
    for p in picks:
        if p.source_sha is None:
            results.append(PickResult(p, 'missing', None, []))
            continue
        files = compare_patches(patches[p.source_sha], patches[p.commit], threshold=threshold)
        if any(f.status in ('left-only', 'right-only') for f in files):
            results.append(PickResult(p, 'files-differ', None, files))
            continue
        levels = [f.level for f in files]
        if None in levels:
            results.append(PickResult(p, 'differs', None, files))
            continue
        level = max(levels, key=level_idx) if levels else 'none'
        results.append(PickResult(p, level_label(level), level, files))
    return results


# Delta / merge invariance

@dataclass
class DeltaResult:
    label: str
    left_range: tuple[str, str]  # (merge-base, tip) before
    right_range: tuple[str, str]  # (merge-base, tip) after
    absorbed: list[str]  # commits that entered the base and duplicate the child's own commits
    files: list[FileResult]

    def sizes(self) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """((files, added, removed) before, (files, added, removed) after)."""
        def size(sides):
            sides = [s for s in sides if s is not None]
            counts = [line_counts(s) for s in sides]
            return len(sides), sum(a for a, _ in counts), sum(d for _, d in counts)
        return size(f.left for f in self.files), size(f.right for f in self.files)

    def ok(self) -> bool:
        return all(f.status in ('equal', 'absorbed', 'adopted') for f in self.files)

    def to_json(self) -> dict:
        (lf, la, ld), (rf, ra, rd) = self.sizes()
        return {
            'label': self.label,
            'before': {'base': self.left_range[0], 'tip': self.left_range[1], 'files': lf, 'added': la, 'removed': ld},
            'after': {'base': self.right_range[0], 'tip': self.right_range[1], 'files': rf, 'added': ra, 'removed': rd},
            'absorbed_commits': self.absorbed,
            'ok': self.ok(),
            'files': [f.to_json() for f in self.files],
        }


def patch_ids(revs: list[str], opts: DiffOpts) -> dict[str, str]:
    """`git patch-id --stable` of each non-merge commit in `revs` (rev-list args), as {commit: patch-id}."""
    log = git('log', '--no-merges', '-p', '--format=commit %H', *opts.flags(), *revs, '--')
    out = git('patch-id', '--stable', input=log)
    return {commit: pid for pid, commit in (line.split() for line in out.splitlines())}


def rev_list(*revs: str) -> list[str]:
    return git('rev-list', *revs, '--').split()


def absorbed_commits(
    own_revs: list[str],
    base_gain_revs: list[str],
    opts: DiffOpts = DiffOpts(),
) -> list[str]:
    """Commits that entered the child's base (`base_gain_revs`) and duplicate one of its own commits (`own_revs`).

    Matched by `-x` trailer in either direction, or by equal `git patch-id --stable`.
    """
    own = set(rev_list(*own_revs))
    gained = rev_list(*base_gain_revs)
    if not own or not gained:
        return []
    own_bodies = {sha: body for sha, _, body in log_bodies('--no-walk=unsorted', *own)}
    gained_bodies = {sha: body for sha, _, body in log_bodies('--no-walk=unsorted', *gained)}
    own_sources = {trailer_source(b) for b in own_bodies.values()} - {None}
    gained_sources = {sha: trailer_source(b) for sha, b in gained_bodies.items()}
    resolved = resolve_commits(sorted(own_sources | (set(gained_sources.values()) - {None})))
    own_sources = {resolved[s] for s in own_sources} - {None}
    own_pids = set(patch_ids(['--no-walk=unsorted', *own], opts).values())
    gained_pids = patch_ids(['--no-walk=unsorted', *gained], opts)
    absorbed = []
    for sha in gained:
        src = gained_sources[sha]
        if (src and resolved[src] in own) or sha in own_sources or gained_pids.get(sha) in own_pids:
            absorbed.append(sha)
    return absorbed


def patch_line_counts(patches: dict[str, str]) -> dict[str, Counter]:
    return {path: Counter(changed_lines(patch)) for path, patch in patches.items()}


def apply_absorption(
    files: list[FileResult],
    absorbed_patches: dict[str, dict[str, str]],
    base_patches: dict[str, str],
) -> None:
    """Mark files whose residual the base's gains explain as `absorbed` or `adopted` (in place).

    A residual `+`/`-` line the delta lost must appear with the same sign among the absorbed picks' changes to that
    file (`absorbed`), or else in the base's net change over the same interval (`adopted`: the parent took the child's
    content some other way, e.g. a squashed or hand-copied commit). A line the delta gained must appear with the
    opposite sign among the absorbed picks (the base now has, or lacks, a line the child's own commits changed);
    gained lines are never explained by the base's net change, so a merge that drops a parent change still `differs`.
    """
    pick_lines: dict[str, Counter] = {}
    for per_file in absorbed_patches.values():
        for path, lines in patch_line_counts(per_file).items():
            pick_lines.setdefault(path, Counter()).update(lines)
    base_lines = patch_line_counts(base_patches)
    flip = {'+': '-', '-': '+'}

    def lines_for(index: dict[str, Counter], f: FileResult) -> Counter:
        lines = index.get(f.path, Counter())
        if f.left_path != f.path:
            lines = lines + index.get(f.left_path, Counter())
        return lines

    for f in files:
        if f.status == 'equal':
            continue
        before = Counter(changed_lines(f.left)) if f.left is not None else Counter()
        after = Counter(changed_lines(f.right)) if f.right is not None else Counter()
        lost, gained = before - after, after - before
        picked, based = lines_for(pick_lines, f), lines_for(base_lines, f)
        by_picks = by_base = 0
        for line, n in lost.items():
            k = min(n, picked[line])
            by_picks += k
            by_base += min(n - k, based[line])
        for line, n in gained.items():
            by_picks += min(n, picked[flip[line[0]] + line[1:]])
        f.residual_lines = sum(lost.values()) + sum(gained.values())
        f.absorbed_lines = by_picks + by_base
        if f.residual_lines and f.absorbed_lines == f.residual_lines:
            f.status = 'adopted' if by_base else 'absorbed'


def compare_delta(
    label: str,
    before: tuple[str, str],
    after: tuple[str, str],
    opts: DiffOpts = DiffOpts(),
    threshold: str = DEFAULT_LEVEL,
) -> DeltaResult:
    """Compare a child's own delta `before = (base, tip)` vs. `after = (base, tip)`, net of absorbed picks."""
    (base_a, tip_a), (base_b, tip_b) = before, after
    left = range_patches(base_a, tip_a, opts=opts)
    right = range_patches(base_b, tip_b, opts=opts)
    files = compare_patches(left, right, threshold=threshold)
    absorbed = absorbed_commits([f'{base_a}..{tip_a}'], [base_b, f'^{tip_a}'], opts)
    base_patches = range_patches(base_a, base_b, opts=opts) if base_a != base_b else {}
    if absorbed or base_patches:
        apply_absorption(files, commit_patches(absorbed, opts), base_patches)
    return DeltaResult(label, before, after, absorbed, files)


def merge_delta(merge: str, opts: DiffOpts = DiffOpts(), threshold: str = DEFAULT_LEVEL) -> DeltaResult:
    """`mb(M^1, M^2)..M^1` vs `M^2..M`: the first parent's own delta, before and after merging the second."""
    parents = git('rev-list', '--parents', '-n1', merge).split()[1:]
    if len(parents) < 2:
        err(f"{merge} is not a merge commit")
        sys.exit(2)
    p1, p2 = parents[:2]
    sha = rev_parse(merge)
    return compare_delta(sha, (merge_base(p1, p2), p1), (p2, sha), opts, threshold)


def expand_merges(revs: tuple[str, ...]) -> list[str]:
    """Merge commits named by `revs`: ranges (`A..B`) expand to their first-parent merges, oldest first."""
    merges = []
    for rev in revs:
        if '..' in rev:
            merges.extend(git('rev-list', '--merges', '--first-parent', '--reverse', rev, '--').split())
        else:
            merges.append(rev)
    return merges
