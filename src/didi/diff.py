import sys
from subprocess import PIPE, Popen, run
from typing import Dict

from utz import err


def get_rename_mapping(
    refspec: str,
    find_renames: str = None,
    find_copies: str = None,
) -> Dict[str, str]:
    """Get file rename/copy mapping for a refspec.

    Returns a dict mapping old paths to new paths for renamed/copied files.
    """
    cmd = ['git', 'diff', '--name-status']
    if find_renames:
        cmd.append(f'-M{find_renames}')
    if find_copies:
        cmd.append(f'-C{find_copies}')
    cmd.append(refspec)

    result = run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return {}

    mapping = {}
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) < 2:
            continue

        status = parts[0]
        # R = rename, C = copy
        # Format: R100\told_path\tnew_path or just R\told_path\tnew_path
        if status.startswith('R') or status.startswith('C'):
            if len(parts) >= 3:
                old_path = parts[1]
                new_path = parts[2]
                mapping[old_path] = new_path

    return mapping


def build_diff_cmd(
    ignore_whitespace: bool = False,
    find_renames: str = None,
    find_copies: str = None,
    follow: bool = False,
) -> list[str]:
    """Build base git diff command with common options."""
    cmd = ['git', 'diff']
    if follow:
        cmd.append('--follow')
    if ignore_whitespace:
        cmd.append('-w')
    if find_renames:
        cmd.append(f'-M{find_renames}')
    if find_copies:
        cmd.append(f'-C{find_copies}')
    return cmd


def get_changed_files(refspec: str, paths: tuple[str, ...] = ()) -> list[str]:
    """Get list of files changed in a git refspec, optionally filtered by paths."""
    cmd = ['git', 'diff', '--name-only', refspec]
    if paths:
        cmd.extend(['--', *paths])
    result = run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err(f"Error getting changed files for {refspec}: {result.stderr.strip()}")
        sys.exit(1)
    return [f for f in result.stdout.strip().split('\n') if f]


def normalize_diff(diff_text: str, path_mapping: Dict[str, str] = None) -> str:
    """Normalize diff text by removing variable parts like index SHAs and mapping paths.

    Args:
        diff_text: The diff text to normalize
        path_mapping: Optional dict mapping old paths to new paths (for renames)

    Returns:
        Normalized diff text
    """
    lines = []
    for line in diff_text.splitlines():
        # Remove index line SHAs: "index abc123..def456" -> "index ..."
        if line.startswith('index '):
            lines.append('index ...')
        # Normalize paths in diff headers if mapping provided
        elif path_mapping and (line.startswith('diff --git ') or
                               line.startswith('--- ') or
                               line.startswith('+++ ')):
            # Apply path mapping to normalize renamed files
            normalized_line = line
            for old_path, new_path in path_mapping.items():
                normalized_line = normalized_line.replace(old_path, new_path)
            lines.append(normalized_line)
        else:
            lines.append(line)
    return '\n'.join(lines)


def get_file_diff(
    refspec: str,
    filepath: str,
    ignore_whitespace: bool = False,
    unified: int = 3,
    find_renames: str = None,
    find_copies: str = None,
) -> str:
    """Get diff for a specific file in a refspec."""
    cmd = build_diff_cmd(ignore_whitespace, find_renames, find_copies, follow=True)
    cmd.extend([f'-U{unified}', refspec, '--', filepath])
    result = run(cmd, capture_output=True, text=True)
    return result.stdout


def get_commits(refspec: str) -> list[str]:
    """Get list of commits in a refspec."""
    result = run(['git', 'log', '--oneline', refspec], capture_output=True, text=True)
    if result.returncode != 0:
        err(f"Error getting commits for {refspec}: {result.stderr}")
        return []
    return [line for line in result.stdout.strip().split('\n') if line]


def parse_refspec_bases(refspec1: str, refspec2: str) -> tuple[str, str, str, str]:
    """Parse two refspecs to extract bases.

    Given refspecs like 'A..B' and 'C..D', returns (A, B, C, D).
    This allows computing A..C to find upstream changes (renames, etc).

    Returns:
        tuple: (base1, tip1, base2, tip2) or empty strings if not parseable
    """
    if '..' not in refspec1 or '..' not in refspec2:
        return ('', '', '', '')

    base1, tip1 = refspec1.split('..', 1)
    base2, tip2 = refspec2.split('..', 1)

    return (base1, tip1, base2, tip2)


def compute_upstream_range(refspec1: str, refspec2: str) -> str:
    """Compute upstream range from two refspecs.

    Given 'A..B' (before rebase) and 'C..D' (after rebase),
    returns 'A..C' (upstream changes).

    Returns empty string if refspecs can't be parsed.
    """
    base1, _, base2, _ = parse_refspec_bases(refspec1, refspec2)
    if base1 and base2:
        return f'{base1}..{base2}'
    return ''


def get_orig_head() -> str:
    """Get ORIG_HEAD (original branch tip before rebase)."""
    git_dir = run(['git', 'rev-parse', '--git-dir'], capture_output=True, text=True)
    if git_dir.returncode != 0:
        err("Not in a git repository")
        sys.exit(1)
    git_dir = git_dir.stdout.strip()

    # Check various locations for orig-head
    for path in [
        f'{git_dir}/ORIG_HEAD',
        f'{git_dir}/rebase-apply/orig-head',
        f'{git_dir}/rebase-merge/orig-head',
    ]:
        try:
            with open(path) as f:
                return f.read().strip()
        except FileNotFoundError:
            continue

    err("Could not find ORIG_HEAD (not in/after a rebase?)")
    sys.exit(1)


def get_onto() -> str:
    """Get onto commit (what we rebased onto).

    During rebase: reads from .git/rebase-merge/onto
    After rebase: parses reflog for most recent 'rebase (start)' entry
    """
    git_dir = run(['git', 'rev-parse', '--git-dir'], capture_output=True, text=True)
    if git_dir.returncode != 0:
        err("Not in a git repository")
        sys.exit(1)
    git_dir = git_dir.stdout.strip()

    # During active rebase
    onto_path = f'{git_dir}/rebase-merge/onto'
    try:
        with open(onto_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        pass

    # Post-rebase: check reflog
    reflog = run(
        ['git', 'reflog', '--format=%H %gs'],
        capture_output=True, text=True,
    )
    if reflog.returncode == 0:
        for line in reflog.stdout.splitlines():
            if 'rebase (start)' in line:
                return line.split()[0]

    err("Could not find onto (not in/after a rebase?)")
    sys.exit(1)


def get_rebase_head() -> str:
    """Get REBASE_HEAD (commit currently being replayed, mid-rebase only)."""
    git_dir = run(['git', 'rev-parse', '--git-dir'], capture_output=True, text=True)
    if git_dir.returncode != 0:
        err("Not in a git repository")
        sys.exit(1)
    git_dir = git_dir.stdout.strip()

    rebase_head = f'{git_dir}/REBASE_HEAD'
    try:
        with open(rebase_head) as f:
            return f.read().strip()
    except FileNotFoundError:
        pass

    err("No REBASE_HEAD (not mid-rebase?)")
    sys.exit(1)


def get_rebase_merge_base() -> str:
    """Get merge-base of orig_head and onto."""
    oh = get_orig_head()
    ot = get_onto()
    result = run(['git', 'merge-base', oh, ot], capture_output=True, text=True)
    if result.returncode != 0:
        err(f"Could not compute merge-base of {oh} and {ot}")
        sys.exit(1)
    return result.stdout.strip()


# Sentinel refspec names and their expansions
REFSPEC_SENTINELS = {'mboh', 'mbot', 'mbrhp', 'mbrp', 'oth'}


def expand_refspec(refspec: str) -> str:
    """Expand sentinel refspecs like 'mboh', 'oth' to actual git ranges.

    Sentinels:
        mboh  = merge-base..orig-head (original feature commits)
        mbot  = merge-base..onto (upstream commits since diverge)
        mbrhp = merge-base..rebase-head^ (original commits processed so far)
        oth   = onto..HEAD (rebased commits)
    """
    if refspec == 'mboh':
        mb = get_rebase_merge_base()
        oh = get_orig_head()
        return f'{mb}..{oh}'
    elif refspec == 'mbot':
        mb = get_rebase_merge_base()
        ot = get_onto()
        return f'{mb}..{ot}'
    elif refspec in ('mbrhp', 'mbrp'):
        mb = get_rebase_merge_base()
        rh = get_rebase_head()
        return f'{mb}..{rh}^'
    elif refspec == 'oth':
        ot = get_onto()
        return f'{ot}..HEAD'
    else:
        return refspec


def get_rebase_refspecs() -> tuple[str, str]:
    """Get refspecs for post-rebase verification: mboh vs oth.

    Returns:
        (mboh, oth) - original commits vs rebased commits
    """
    return expand_refspec('mboh'), expand_refspec('oth')


def parse_paths(paths: tuple[str, ...]) -> list[str]:
    """Parse path arguments, expanding @file and stdin references.

    Supports:
    - Direct paths: 'file.txt'
    - Read from file: '@paths.txt'
    - Read from stdin: '-' or '@-'

    Returns list of paths.
    """
    result = []
    for path in paths:
        if path == '-' or path == '@-':
            # Read paths from stdin
            for line in sys.stdin:
                line = line.strip()
                if line:
                    result.append(line)
        elif path.startswith('@'):
            # Read paths from file
            filepath = path[1:]
            try:
                with open(filepath) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            result.append(line)
            except FileNotFoundError:
                err(f"Path file not found: {filepath}")
                sys.exit(1)
        else:
            result.append(path)
    return result


def parse_refspec_endpoints(refspec: str) -> tuple[str, str]:
    """Parse a refspec into start and end points.

    'A..B' -> ('A', 'B')
    'A' -> ('A^', 'A')
    """
    if '..' in refspec:
        start, end = refspec.split('..', 1)
    else:
        start, end = f'{refspec}^', refspec
    return start, end


def run_pipeline(cmds: list[str]) -> str:
    """Run a sequence of shell commands piped together, return stdout.

    Args:
        cmds: List of shell commands to pipe together

    Returns:
        stdout output as string
    """
    if not cmds:
        return ''

    # Build the pipeline: cmd1 | cmd2 | cmd3 ...
    pipeline_str = ' | '.join(cmds)
    result = run(pipeline_str, shell=True, capture_output=True, text=True)

    if result.returncode != 0 and result.stderr:
        err(f"Pipeline failed: {result.stderr.strip()}")

    return result.stdout


def get_file_diff_with_pipeline(
    refspec: str,
    filepath: str,
    pipeline_cmds: list[str],
    ignore_whitespace: bool = False,
    unified: int = 3,
) -> str:
    """Get diff for a file, running content through pipeline first.

    For a refspec like A..B and pipeline ['pqa'], this:
    1. Gets file content at A, runs through pipeline
    2. Gets file content at B, runs through pipeline
    3. Diffs the two pipeline outputs

    Returns the diff output as a string.
    """
    start, end = parse_refspec_endpoints(refspec)

    # Build pipeline commands for each side
    # First command gets file content from git
    cmd_start = f'git show {start}:{filepath}'
    cmd_end = f'git show {end}:{filepath}'

    cmds1 = [cmd_start, *pipeline_cmds]
    cmds2 = [cmd_end, *pipeline_cmds]

    # Run both pipelines and capture output
    output1 = run_pipeline(cmds1)
    output2 = run_pipeline(cmds2)

    # Use difflib to generate unified diff
    import difflib
    diff_lines = list(difflib.unified_diff(
        output1.splitlines(keepends=True),
        output2.splitlines(keepends=True),
        fromfile=f'a/{filepath}',
        tofile=f'b/{filepath}',
        n=unified,
    ))

    return ''.join(diff_lines)
