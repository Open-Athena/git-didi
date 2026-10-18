"""Split multi-file patches, and normalize per-file patches at increasing levels of leniency."""

import re
from codecs import escape_decode
from collections import Counter
from difflib import SequenceMatcher
from typing import Optional

# Cumulative: each level ignores everything the previous ones do, plus more.
LEVELS = ('none', 'index', 'lines', 'context', 'ws', 'tokens')
DEFAULT_LEVEL = 'index'

HUNK_RE = re.compile(r'^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$')
TOKEN_RE = re.compile(r'\w+|[^\w\s]')
WS_RE = re.compile(r'\s+')


def level_idx(level: str) -> int:
    return LEVELS.index(level)


def unquote_path(path: str) -> str:
    """Undo git's C-style quoting of paths containing special characters."""
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        return escape_decode(path[1:-1].encode())[0].decode('utf-8')
    return path


def strip_prefix(path: str, prefix: str) -> str:
    path = unquote_path(path.rstrip('\t'))
    return path[len(prefix):] if path.startswith(prefix) else path


def patch_paths(lines: list[str]) -> tuple[str, str]:
    """Return a per-file patch's (old path, new path), from its header lines."""
    old = new = None
    for line in lines:
        if line.startswith('@@'):
            break
        if line.startswith('rename from ') or line.startswith('copy from '):
            old = unquote_path(line.split(' ', 2)[2])
        elif line.startswith('rename to ') or line.startswith('copy to '):
            new = unquote_path(line.split(' ', 2)[2])
        elif line.startswith('--- '):
            path = line[4:]
            if path != '/dev/null':
                old = old or strip_prefix(path, 'a/')
        elif line.startswith('+++ '):
            path = line[4:]
            if path != '/dev/null':
                new = new or strip_prefix(path, 'b/')
    if old is None and new is None:
        # Binary or mode-only change: only the `diff --git a/X b/X` line names the file.
        spec = lines[0][len('diff --git '):]
        if spec.startswith('"'):
            end = spec.index('" ', 1) + 1
            old, new = strip_prefix(spec[:end], 'a/'), strip_prefix(spec[end + 1:], 'b/')
        else:
            half = (len(spec) - 1) // 2
            old, new = strip_prefix(spec[:half], 'a/'), strip_prefix(spec[half + 1:], 'b/')
    return old or new, new or old


def split_patch(text: str) -> dict[str, str]:
    """Split `git diff` output into per-file patches, keyed by (new) path."""
    patches = {}
    block: list[str] = []

    def flush():
        if block:
            _, new = patch_paths(block)
            patches[new] = '\n'.join(block)

    for line in text.splitlines():
        if line.startswith('diff --git '):
            flush()
            block = [line]
        elif block:
            block.append(line)
    flush()
    return patches


def map_paths(line: str, path_mapping: Optional[dict[str, str]]) -> str:
    if path_mapping:
        for old_path, new_path in path_mapping.items():
            line = line.replace(old_path, new_path)
    return line


def changed_lines(patch: str) -> list[str]:
    """`+`/`-` lines of a per-file patch (excluding `---`/`+++` headers)."""
    out = []
    in_hunk = False
    for line in patch.splitlines():
        if line.startswith('@@'):
            in_hunk = True
        elif in_hunk and line[:1] in ('+', '-'):
            out.append(line)
    return out


def line_counts(patch: Optional[str]) -> tuple[int, int]:
    """(added, removed) line counts of a per-file patch."""
    if patch is None:
        return 0, 0
    counts = Counter(line[0] for line in changed_lines(patch))
    return counts['+'], counts['-']


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def token_edits(removed: list[str], added: list[str]) -> list[str]:
    """Express a block of `-` lines → `+` lines as a whitespace-insensitive token edit script."""
    a = tokens(' '.join(removed))
    b = tokens(' '.join(added))
    edits = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        old, new = ' '.join(a[i1:i2]), ' '.join(b[j1:j2])
        if tag == 'insert':
            edits.append(f'insert: {new}')
        elif tag == 'delete':
            edits.append(f'delete: {old}')
        elif tag == 'replace':
            edits.append(f'replace: {old} → {new}')
    return edits


def normalize(
    patch: str,
    level: str = DEFAULT_LEVEL,
    path_mapping: Optional[dict[str, str]] = None,
) -> list[str]:
    """Normalize a per-file patch to a list of lines comparable at `level`.

    Levels (cumulative):
        none:    as-is
        index:   `index <sha>..<sha>` header lines blanked
        lines:   hunk-header line numbers dropped (`@@ -a,b +c,d @@ ctx` → `@@ ctx`)
        context: context lines and hunk headers dropped (ordered `+`/`-` lines only)
        ws:      whitespace within changed lines dropped
        tokens:  each block of `-`/`+` lines becomes a token-level edit script
    """
    n = level_idx(level)
    out = []
    in_hunk = False
    removed: list[str] = []
    added: list[str] = []

    def flush_block():
        if removed or added:
            out.extend(token_edits(removed, added))
            removed.clear()
            added.clear()

    for line in patch.splitlines():
        if not in_hunk and not line.startswith('@@'):
            # File header
            if n >= level_idx('index') and line.startswith('index '):
                line = 'index ...'
            elif line.startswith(('diff --git ', '--- ', '+++ ', 'rename ', 'copy ')):
                line = map_paths(line, path_mapping)
            out.append(line)
            continue
        if line.startswith('@@'):
            in_hunk = True
            if n >= level_idx('tokens'):
                flush_block()
            if n >= level_idx('context'):
                continue
            if n >= level_idx('lines'):
                m = HUNK_RE.match(line)
                if m:
                    line = f'@@ {m.group(1)}'.rstrip()
            out.append(line)
            continue
        kind = line[:1]
        if n >= level_idx('tokens'):
            if kind == '-':
                removed.append(line[1:])
            elif kind == '+':
                added.append(line[1:])
            elif kind == ' ':
                flush_block()
            continue
        if kind == ' ' and n >= level_idx('context'):
            continue
        if kind in ('+', '-') and n >= level_idx('ws'):
            line = kind + WS_RE.sub('', line[1:])
        out.append(line)
    if n >= level_idx('tokens'):
        flush_block()
    return out


def first_equal_level(
    left: str,
    right: str,
    path_mapping: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """The least-lenient level at which two per-file patches are equal (None if they differ at every level)."""
    for level in LEVELS:
        if normalize(left, level, path_mapping) == normalize(right, level):
            return level
    return None


def level_label(level: Optional[str]) -> str:
    """Summary label for a file/commit first equal at `level`; `index`-only differences are pure noise."""
    if level is None:
        return 'differs'
    if level_idx(level) <= level_idx('index'):
        return 'equal'
    return f'equal@{level}'
