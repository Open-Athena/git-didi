#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "click",
#     "utz",
# ]
# ///
"""Compare diffs between two git ranges - a 'diff of diffs' tool.

This tool is particularly useful for verifying rebases, especially when complex
conflict resolution was involved. It helps ensure that the actual changes in your
branch remain the same before and after rebasing onto a new upstream.

Common use case - checking a rebase:

After fetching and rebasing your branch onto main:
    git fetch
    git rebase main

You can verify the rebase preserved your changes:
    git-didi patch main@{1}..branch@{1} main..branch

This compares:
- Left side: Your changes before the rebase (main@{1}..branch@{1})
- Right side: Your changes after the rebase (main..branch)

The @{1} syntax refers to the previous position in the reflog. If both refs
moved exactly once during the rebase, @{1} will work. If you've done multiple
operations, you may need @{2}, @{3}, etc. Use `git reflog` to find the right
positions.

Aliases for common operations:
- gdds: Compare diff --stat output
- gddp: Compare patches file by file
- gddc: Compare individual commits

The tool automatically filters out spurious differences like git index SHAs
that change even when the actual patch content is identical.
"""

import difflib
import json
import sys
from subprocess import run

from click import Choice, echo, group, option, style
from utz import err
from utz.cli import arg, flag, opt

from ._version import get_version_info
from .color import should_use_color
from .compare import (
    DeltaResult,
    DiffOpts,
    classify_picks,
    compare_delta,
    compare_patches,
    display_lines,
    expand_merges,
    find_picks,
    git,
    merge_base,
    merge_delta,
    range_patches,
    rev_parse,
    summarize,
)
from .diff import (
    build_diff_cmd,
    compute_upstream_range,
    expand_refspec,
    get_changed_files,
    get_commits,
    get_file_diff,
    get_file_diff_with_pipeline,
    get_rebase_refspecs,
    get_rename_mapping,
    normalize_diff,
    parse_paths,
)
from .normalize import DEFAULT_LEVEL, LEVELS
from .pager import Pager
from .report import build_report, load_config


# Common option decorators
color_opt = opt('-c', '--color', type=Choice(['auto', 'always', 'never']), default='auto', help='When to use colored output (default: auto)')
pager_opt = opt('--pager', type=Choice(['auto', 'always', 'never']), default='auto', help='When to use pager (default: auto)')
find_copies_opt = opt('-C', '--find-copies', type=str, metavar='[<n>]', help='Detect copies as well as renames (similarity threshold, e.g., 50% or 0.5)')
find_renames_opt = opt('-M', '--find-renames', type=str, metavar='[<n>]', help='Detect renames (similarity threshold, e.g., 50% or 0.5)')
ignore_whitespace_flag = flag('-w', '--ignore-whitespace', help='Pass -w to git diff commands to ignore whitespace')
json_flag = flag('-j', '--json', 'json_out', help='Print one JSON record per file (or commit), instead of diffs')
normalize_opt = opt('-n', '--normalize', 'normalize_level', type=Choice(LEVELS), default=DEFAULT_LEVEL, help=f'Normalization level at which patches count as equal (cumulative: {", ".join(LEVELS)}; default: {DEFAULT_LEVEL})')


def common_opts(func):
    """Apply common options to all commands."""
    func = color_opt(func)
    func = pager_opt(func)
    func = find_copies_opt(func)
    func = find_renames_opt(func)
    func = ignore_whitespace_flag(func)
    return func


def print_version(ctx, param, value):
    """Print version and exit."""
    if not value or ctx.resilient_parsing:
        return
    echo(get_version_info())
    ctx.exit()


def echo_didi_line(line: str, use_color: bool) -> None:
    """Print one line of a diff of diffs, coloring the outer and nested diff markers."""
    if not use_color:
        echo(line)
        return

    # Handle unified diff headers from outer diff first (---, +++, @@)
    # These are lines from the outer diff, not nested patterns
    if line.startswith('---') and not line.startswith('----'):
        # Real outer diff header - red fg
        echo(style(line, fg='red'), color=True)
        return
    elif line.startswith('+++') and not line.startswith('++++'):
        # Real outer diff header - green fg
        echo(style(line, fg='green'), color=True)
        return
    elif line.startswith('@@') and not (line.startswith('-@@') or line.startswith('+@@') or line.startswith(' @@')):
        # Hunk header from outer diff (not nested)
        echo(style(line, fg='cyan'), color=True)
        return
    elif line.startswith('-@@') or line.startswith('-index ') or line.startswith('-diff ') or line.startswith('----') or line.startswith('-+++'):
        # Nested metadata in removed section - red fg only
        echo(style(line, fg='red'), color=True)
        return
    elif line.startswith('+@@') or line.startswith('+index ') or line.startswith('+diff ') or line.startswith('++++') or line.startswith('+---'):
        # Nested metadata in added section - green fg only
        echo(style(line, fg='green'), color=True)
        return

    # Handle nested diff patterns (diff of diffs)
    # Use 256-color palette matching Claude's diff colors
    # Greens: 28 (brighter #00a858-like), 22 (darker #005e25-like)
    # Reds: 161 (brighter #c1536a-like), 88 (darker #852135-like)
    # Background determined by FIRST char: + = green, - = red
    # First 2 chars get their own bg colors based on symbols
    # All lines in the nested diff have outer prefix (+, -, or space)
    if len(line) >= 2 and line[0] in '+-':
        prefix = line[:2]
        rest = line[2:]

        # Determine prefix backgrounds based on each char
        prefix_bg = []
        for char in prefix:
            if char == '+':
                prefix_bg.append('28')  # bright green
            elif char == '-':
                prefix_bg.append('161')  # bright red
            else:  # space
                prefix_bg.append('0')  # black/clear

        # Determine line background based on first char
        if line[0] == '+':
            line_bg_bright = '28'
            line_bg_dark = '22'
            line_bg_vdark = '23'
        else:  # '-'
            line_bg_bright = '161'
            line_bg_dark = '88'
            line_bg_vdark = '52'

        if line.startswith('++'):
            # Added line in added section - white on bright green (bold)
            print(f'\033[1;38;5;231;48;5;{prefix_bg[0]}m{prefix[0]}\033[48;5;{prefix_bg[1]}m{prefix[1]}\033[48;5;{line_bg_bright}m{rest}\033[0m')
        elif line.startswith('--'):
            # Removed line in removed section - white on bright red (bold)
            print(f'\033[1;38;5;231;48;5;{prefix_bg[0]}m{prefix[0]}\033[48;5;{prefix_bg[1]}m{prefix[1]}\033[48;5;{line_bg_bright}m{rest}\033[0m')
        elif line.startswith('+ '):
            # Context line in added section - white on very dark green
            print(f'\033[38;5;231;48;5;{prefix_bg[0]}m{prefix[0]}\033[48;5;{prefix_bg[1]}m{prefix[1]}\033[48;5;{line_bg_vdark}m{rest}\033[0m')
        elif line.startswith('- '):
            # Context line in removed section - white on very dark red
            print(f'\033[38;5;231;48;5;{prefix_bg[0]}m{prefix[0]}\033[48;5;{prefix_bg[1]}m{prefix[1]}\033[48;5;{line_bg_vdark}m{rest}\033[0m')
        elif line.startswith('+-'):
            # Line in added section (+ first char = green bg)
            print(f'\033[38;5;231;48;5;{prefix_bg[0]}m{prefix[0]}\033[48;5;{prefix_bg[1]}m{prefix[1]}\033[48;5;{line_bg_dark}m{rest}\033[0m')
        elif line.startswith('-+'):
            # Line in removed section (- first char = red bg)
            print(f'\033[38;5;231;48;5;{prefix_bg[0]}m{prefix[0]}\033[48;5;{prefix_bg[1]}m{prefix[1]}\033[48;5;{line_bg_dark}m{rest}\033[0m')
        elif line.startswith('+'):
            # Any other line in added section (e.g., +diff, +index)
            print(f'\033[38;5;231;48;5;{prefix_bg[0]}m{prefix[0]}\033[48;5;{prefix_bg[1] if len(prefix) > 1 else line_bg_dark}m{prefix[1] if len(prefix) > 1 else ""}\033[48;5;{line_bg_dark}m{rest}\033[0m')
        elif line.startswith('-'):
            # Any other line in removed section (e.g., -diff, -index)
            print(f'\033[38;5;231;48;5;{prefix_bg[0]}m{prefix[0]}\033[48;5;{prefix_bg[1] if len(prefix) > 1 else line_bg_dark}m{prefix[1] if len(prefix) > 1 else ""}\033[48;5;{line_bg_dark}m{rest}\033[0m')
        else:
            echo(line)
    elif line.startswith(' '):
        # Context line from outer diff (no color)
        echo(line)
    else:
        echo(line)


@group()
@option('-V', '--version', is_flag=True, callback=print_version, expose_value=False, is_eager=True, help='Show version and exit')
def cli():
    """Compare git diffs between two ranges.

    Useful for comparing changes before and after a rebase.
    """
    pass


# Register shell-integration command
from .commands import shell_integration as shell_integration_module
shell_integration_module.register(cli)


@cli.command()
@common_opts
@flag('-r', '--rebase', help='Rebase mode: auto-detect refspecs (mboh oth)')
@arg('args', nargs=-1)
def stat(
    color: str,
    pager: str,
    find_copies: str,
    find_renames: str,
    ignore_whitespace: bool,
    rebase: bool,
    args: tuple[str, ...],
) -> None:
    """Compare git diff --stat output between two refspecs.

    \b
    Standard mode:
        git-didi stat REFSPEC1 REFSPEC2 [PATHS...]

    \b
    Rebase mode (-r, shorthand for "mboh oth"):
        git-didi stat -r [PATHS...]

    \b
    Sentinel refspecs (expanded automatically):
        mboh, mbot, mbrhp/mbrp, oth (see "patch --help" for details)
    """
    # Parse refspecs based on mode
    if rebase:
        refspec1, refspec2 = get_rebase_refspecs()
        paths = args
    else:
        if len(args) < 2:
            err("Expected two refspecs (or use -r for rebase mode)")
            sys.exit(2)
        refspec1 = expand_refspec(args[0])
        refspec2 = expand_refspec(args[1])
        paths = args[2:]

    use_color = should_use_color(color)

    with Pager(pager):
        # Compute upstream range to detect renames
        upstream_range = compute_upstream_range(refspec1, refspec2)
        rename_map = {}
        if upstream_range:
            rename_map = get_rename_mapping(upstream_range, find_renames, find_copies)
            if rename_map:
                err(f"Detected {len(rename_map)} rename(s) in upstream ({upstream_range})")

        # Use --numstat for machine-readable output (fixed format, no spacing issues)
        # Only use --follow when filtering to a single path
        use_follow = len(paths) == 1
        cmd1 = build_diff_cmd(ignore_whitespace, find_renames, find_copies, follow=use_follow)
        cmd1.extend(['--numstat', refspec1])
        if paths:
            cmd1.extend(['--', *paths])
        result1 = run(cmd1, capture_output=True, text=True)
        if result1.returncode != 0:
            err(f"Error getting diff for {refspec1}: {result1.stderr}")
            sys.exit(2)

        cmd2 = build_diff_cmd(ignore_whitespace, find_renames, find_copies, follow=use_follow)
        cmd2.extend(['--numstat', refspec2])
        if paths:
            cmd2.extend(['--', *paths])
        result2 = run(cmd2, capture_output=True, text=True)
        if result2.returncode != 0:
            err(f"Error getting diff for {refspec2}: {result2.stderr}")
            sys.exit(2)

        lines1 = result1.stdout.splitlines()
        lines2 = result2.stdout.splitlines()

        # Apply rename mapping to lines1
        # numstat format: "added\tdeleted\tfilename"
        if rename_map:
            normalized_lines1 = []
            for line in lines1:
                if '\t' in line:
                    parts = line.split('\t', 2)
                    if len(parts) == 3:
                        added, deleted, filepath = parts
                        # Apply rename mapping
                        if filepath in rename_map:
                            filepath = rename_map[filepath]
                        normalized_lines1.append(f'{added}\t{deleted}\t{filepath}')
                    else:
                        normalized_lines1.append(line)
                else:
                    normalized_lines1.append(line)
            lines1 = normalized_lines1

        # Sort both for consistent comparison (git may output in different orders)
        lines1 = sorted(lines1)
        lines2 = sorted(lines2)

        # Show unified diff
        diff = difflib.unified_diff(
            lines1,
            lines2,
            fromfile=f'git diff --numstat {refspec1}',
            tofile=f'git diff --numstat {refspec2}',
            lineterm=''
        )

        has_changes = False
        for line in diff:
            has_changes = True
            if use_color:
                if line.startswith('+'):
                    echo(style(line, fg='green'), color=True)
                elif line.startswith('-'):
                    echo(style(line, fg='red'), color=True)
                elif line.startswith('@'):
                    echo(style(line, fg='cyan'), color=True)
                else:
                    echo(line)
            else:
                echo(line)

        if not has_changes:
            err("No differences in diff stats")
        else:
            sys.exit(1)


@cli.command()
@common_opts
@opt('-p', '--path', multiple=True, help='Paths to compare (enables pipeline mode; supports @file and - for stdin)')
@flag('-r', '--rebase', help='Rebase mode: auto-detect refspecs via git ours-refspec/theirs-refspec')
@opt('-U', '--unified', type=int, default=3, help='Number of context lines to show (default: 3)')
@flag('-q', '--quiet', help='Only show files with differences')
@json_flag
@normalize_opt
@arg('args', nargs=-1)
def patch(
    color: str,
    pager: str,
    find_copies: str,
    find_renames: str,
    path: tuple[str, ...],
    rebase: bool,
    unified: int,
    quiet: bool,
    json_out: bool,
    normalize_level: str,
    ignore_whitespace: bool,
    args: tuple[str, ...],
) -> None:
    """Compare patches file-by-file between two refspecs.

    \b
    Standard mode (refspecs as first two args):
        git-didi patch REFSPEC1 REFSPEC2 [PATHS...]

    \b
    Pipeline mode (with -p):
        git-didi patch REFSPEC1 REFSPEC2 PIPELINE_CMD... -p PATH [-p PATH...]

    \b
    Rebase mode (-r, shorthand for "mboh oth"):
        git-didi patch -r [PATHS...]
        git-didi patch -r PIPELINE_CMD... -p PATH [-p PATH...]

    \b
    Sentinel refspecs (expanded automatically):
        mboh       = merge-base..orig-head (original feature commits)
        mbot       = merge-base..onto (upstream commits since diverge)
        mbrhp/mbrp = merge-base..rebase-head^ (mid-rebase: processed so far)
        oth        = onto..HEAD (rebased commits)

    \b
    Examples with sentinels:
        git-didi patch mboh oth              # post-rebase verify
        git-didi patch mboh mbot             # feature vs upstream
        git-didi patch mbrhp oth             # mid-rebase progress
        git-didi patch mboh oth pqa -p data.parquet  # with pipeline

    \b
    Normalization (-n, cumulative; each file is also classified by the
    least-lenient level at which its two patches are equal):
        none     compare patches as-is
        index    ignore `index <sha>..<sha>` lines (default)
        lines    also ignore hunk-header line numbers
        context  also ignore context lines (compare ordered +/- lines)
        ws       also ignore whitespace within changed lines
        tokens   also compare token-level edit scripts, not whole lines

    Exits 1 if any file differs (at the -n level), 2 on error.
    """
    # Parse refspecs based on mode
    if rebase:
        # Rebase mode: shorthand for mboh oth
        refspec1, refspec2 = get_rebase_refspecs()
        remaining_args = args
    else:
        # Standard mode: first two args are refspecs
        if len(args) < 2:
            err("Expected two refspecs (or use -r for rebase mode)")
            sys.exit(2)
        # Expand sentinel refspecs
        refspec1 = expand_refspec(args[0])
        refspec2 = expand_refspec(args[1])
        remaining_args = args[2:]

    # Determine mode: pipeline vs backward-compatible
    if path:
        # Pipeline mode: remaining args are pipeline commands, paths from -p option
        pipeline_cmds = list(remaining_args)
        paths = tuple(parse_paths(path))
        if not pipeline_cmds:
            err("Pipeline mode requires at least one pipeline command")
            sys.exit(2)
    else:
        # Backward compatible: remaining args are paths, no pipeline
        pipeline_cmds = []
        paths = remaining_args

    # Determine color BEFORE pager redirects stdout
    use_color = should_use_color(color)

    with Pager(pager):
        if pipeline_cmds:
            # Pipeline mode: compare pipeline-processed diffs
            left, right = {}, {}
            for filepath in paths:
                for refspec, patches in ((refspec1, left), (refspec2, right)):
                    patches[filepath] = get_file_diff_with_pipeline(
                        refspec, filepath, pipeline_cmds,
                        ignore_whitespace=ignore_whitespace,
                        unified=unified,
                    )
            rename_map = {}
        else:
            # Compute upstream range to detect renames
            # E.g., if comparing A..B vs C..D, look at A..C for upstream changes
            upstream_range = compute_upstream_range(refspec1, refspec2)
            rename_map = {}
            if upstream_range:
                rename_map = get_rename_mapping(upstream_range, find_renames, find_copies)
                if rename_map:
                    err(f"Detected {len(rename_map)} rename(s) in upstream ({upstream_range})")
            opts = DiffOpts(ignore_whitespace, unified, find_renames, find_copies)
            left = range_patches(refspec1, opts=opts, paths=paths)
            right = range_patches(refspec2, opts=opts, paths=paths)

        results = compare_patches(left, right, rename_map, threshold=normalize_level)
        different = [r for r in results if r.status != 'equal']

        if json_out:
            echo(json.dumps([r.to_json() for r in results], indent=2))
        elif not quiet:
            for r in different:
                # E.g. "(equal@context)": differs at the `-n` level, but would be equal at a more lenient one
                hint = f" ({r.label})" if r.label != 'differs' else ''
                rule = f"\n{'='*60}"
                echo(style(rule, fg='blue') if use_color else rule)
                header = f"File: {r.display_name}{hint}"
                echo(style(header, fg='yellow', bold=True) if use_color else header)
                echo(style('='*60, fg='blue') if use_color else '='*60)
                left_lines, right_lines = display_lines(r, normalize_level)
                for line in difflib.unified_diff(
                    left_lines,
                    right_lines,
                    fromfile=f'{r.left_path} in {refspec1}',
                    tofile=f'{r.path} in {refspec2}',
                    lineterm='',
                ):
                    echo_didi_line(line, use_color)
        elif different:
            echo(style("\nFiles with different patches:", fg='yellow', bold=True) if use_color else "\nFiles with different patches:")
            for r in different:
                echo(f"  {r.display_name} ({r.label})")

        if results:
            err(f"\n{summarize([r.label for r in results])}")
        if not different:
            err("No differences in patches")
        else:
            err(f"{len(different)} file(s) have different patches")
            sys.exit(1)


@cli.command()
@color_opt
def swatches(color: str) -> None:
    """Display color swatches for all 6 nested diff patterns.

    Shows example lines formatted as they would appear in a diff-of-diffs,
    demonstrating all possible combinations of outer and inner diff markers.
    """
    use_color = should_use_color(color)

    if not use_color:
        err("Color swatches require color output. Use --color=always")
        return

    echo(style("\ngddp Color Swatches - Diff of Diffs Patterns", fg='yellow', bold=True))
    echo(style("=" * 50, fg='blue'))
    echo("\nSimulated diff-of-diffs output showing all 6 patterns:\n")

    # Simulate a diff context
    echo(style("@@ -10,6 +10,6 @@ def example():", fg='cyan'))

    # ++ pattern
    prefix = '++'
    rest = 'version = "2.0.0"  # Added line in added section'
    print(f'\033[1;38;5;231;48;5;28m{prefix[0]}\033[48;5;28m{prefix[1]}\033[48;5;28m{rest}\033[0m')

    # -- pattern
    prefix = '--'
    rest = 'version = "1.0.0"  # Removed line in removed section'
    print(f'\033[1;38;5;231;48;5;161m{prefix[0]}\033[48;5;161m{prefix[1]}\033[48;5;161m{rest}\033[0m')

    # + (space) pattern
    prefix = '+ '
    rest = 'author = "example"  # Context in added section'
    print(f'\033[38;5;231;48;5;28m{prefix[0]}\033[48;5;0m{prefix[1]}\033[48;5;23m{rest}\033[0m')

    # - (space) pattern
    prefix = '- '
    rest = 'license = "MIT"  # Context in removed section'
    print(f'\033[38;5;231;48;5;161m{prefix[0]}\033[48;5;0m{prefix[1]}\033[48;5;52m{rest}\033[0m')

    # +- pattern
    prefix = '+-'
    rest = 'status = "deprecated"  # Line type changed (+- mixed)'
    print(f'\033[38;5;231;48;5;28m{prefix[0]}\033[48;5;161m{prefix[1]}\033[48;5;22m{rest}\033[0m')

    # -+ pattern
    prefix = '-+'
    rest = 'status = "active"  # Line type changed (-+ mixed)'
    print(f'\033[38;5;231;48;5;161m{prefix[0]}\033[48;5;28m{prefix[1]}\033[48;5;88m{rest}\033[0m')

    echo("\n" + style("Color Key:", fg='yellow', bold=True))
    echo("  First 2 chars: Individual bg colors per symbol")
    echo("    + → bright green (28)")
    echo("    - → bright red (161)")
    echo("    (space) → black/clear (0)")
    echo("\n  Rest of line: Background based on first char")
    echo("    ++ → bright green (28, bold)")
    echo("    -- → bright red (161, bold)")
    echo("    + (space) → very dark green (23)")
    echo("    - (space) → very dark red (52)")
    echo("    +- → dark green (22)")
    echo("    -+ → dark red (88)")
    echo()


@cli.command()
@common_opts
@opt('-U', '--unified', type=int, default=3, help='Number of context lines to show (default: 3)')
@flag('-r', '--rebase', help='Rebase mode: auto-detect refspecs (mboh oth)')
@arg('args', nargs=-1)
def commits(
    color: str,
    pager: str,
    find_copies: str,
    find_renames: str,
    unified: int,
    ignore_whitespace: bool,
    rebase: bool,
    args: tuple[str, ...],
) -> None:
    """Compare commits between two refspecs.

    First verifies that commits correspond (same count and messages),
    then shows per-commit differences.

    \b
    Standard mode:
        git-didi commits REFSPEC1 REFSPEC2

    \b
    Rebase mode (-r, shorthand for "mboh oth"):
        git-didi commits -r

    \b
    Sentinel refspecs (expanded automatically):
        mboh, mbot, mbrhp/mbrp, oth (see "patch --help" for details)
    """
    # Parse refspecs based on mode
    if rebase:
        refspec1, refspec2 = get_rebase_refspecs()
    else:
        if len(args) < 2:
            err("Expected two refspecs (or use -r for rebase mode)")
            sys.exit(2)
        refspec1 = expand_refspec(args[0])
        refspec2 = expand_refspec(args[1])

    use_color = should_use_color(color)

    with Pager(pager):
        # Get commit info for both refspecs
        commits1 = get_commits(refspec1)
        commits2 = get_commits(refspec2)

        if len(commits1) != len(commits2):
            err(f"Different number of commits: {len(commits1)} in {refspec1}, {len(commits2)} in {refspec2}")

        # Compare commit messages
        echo(style("Comparing commits:", fg='yellow', bold=True) if use_color else "Comparing commits:")
        for i, (c1, c2) in enumerate(zip(commits1, commits2)):
            sha1, msg1 = c1.split(' ', 1)
            sha2, msg2 = c2.split(' ', 1)

            if msg1 == msg2:
                echo(f"  [{i+1}] ✓ {msg1}")
            else:
                echo(style(f"  [{i+1}] ✗ Messages differ:", fg='red') if use_color else f"  [{i+1}] ✗ Messages differ:")
                echo(f"    {refspec1}: {msg1}")
                echo(f"    {refspec2}: {msg2}")

        # Compare each commit's changes
        echo(style("\nComparing commit patches:", fg='yellow', bold=True) if use_color else "\nComparing commit patches:")

        for i, (c1, c2) in enumerate(zip(commits1, commits2)):
            sha1 = c1.split(' ', 1)[0]
            sha2 = c2.split(' ', 1)[0]
            msg = c1.split(' ', 1)[1]

            # Get diff for each commit
            cmd1 = ['git', 'diff']
            if ignore_whitespace:
                cmd1.append('-w')
            cmd1.extend([f'{sha1}^', sha1])
            diff1 = run(cmd1, capture_output=True, text=True).stdout

            cmd2 = ['git', 'diff']
            if ignore_whitespace:
                cmd2.append('-w')
            cmd2.extend([f'{sha2}^', sha2])
            diff2 = run(cmd2, capture_output=True, text=True).stdout

            # Normalize to ignore index SHAs
            norm_diff1 = normalize_diff(diff1)
            norm_diff2 = normalize_diff(diff2)

            if norm_diff1 != norm_diff2:
                echo(style(f"\n[{i+1}] {msg} - DIFFERS", fg='red', bold=True) if use_color else f"\n[{i+1}] {msg} - DIFFERS")

                # Show file-by-file differences for this commit
                files1 = get_changed_files(f'{sha1}^..{sha1}')
                files2 = get_changed_files(f'{sha2}^..{sha2}')
                all_files = sorted(set(files1) | set(files2))

                for filepath in all_files:
                    file_diff1 = get_file_diff(f'{sha1}^..{sha1}', filepath, ignore_whitespace, unified, find_renames, find_copies)
                    file_diff2 = get_file_diff(f'{sha2}^..{sha2}', filepath, ignore_whitespace, unified, find_renames, find_copies)

                    # Normalize to ignore index SHAs
                    if normalize_diff(file_diff1) != normalize_diff(file_diff2):
                        echo(f"    {filepath}: patches differ")
            else:
                echo(f"[{i+1}] {msg} - identical")


@cli.command()
@opt('-C', '--find-copies', type=str, metavar='[<n>]', help='Detect copies as well as renames (similarity threshold, e.g., 50% or 0.5)')
@json_flag
@opt('-M', '--find-renames', type=str, metavar='[<n>]', help='Detect renames (similarity threshold, e.g., 50% or 0.5)')
@normalize_opt
@opt('-s', '--since', help='Only consider picks committed since this date (passed to `git log --since`)')
@opt('-U', '--unified', type=int, default=3, help='Number of context lines in compared patches (default: 3)')
@flag('-v', '--verbose', help='List every pick (and each non-equal file), not just non-equal picks')
@flag('-w', '--ignore-whitespace', help='Pass -w to git diff commands to ignore whitespace')
@arg('revs', nargs=-1)
def picks(
    find_copies: str,
    json_out: bool,
    find_renames: str,
    normalize_level: str,
    since: str,
    unified: int,
    verbose: bool,
    ignore_whitespace: bool,
    revs: tuple[str, ...],
) -> None:
    """Check that cherry-picks are the same patch as their sources.

    For each commit in REVS (`git log` args; default HEAD) with a
    "(cherry picked from commit X)" trailer, compare X's patch with the pick's,
    and classify it as equal, equal@<level> (see "patch --help"), differs,
    files-differ (different file sets, e.g. an adapted pick), or missing
    (source not in the repo, e.g. rewritten history).

    Prints one line per pick that isn't equal at the -n level, and a summary.
    Exits 1 if any such pick exists (missing sources don't count), 2 on error.

    \b
    Examples:
        git-didi picks main..feature
        git-didi picks -s 2026-09-28 gcs cw hf
    """
    opts = DiffOpts(ignore_whitespace, unified, find_renames, find_copies)
    results = classify_picks(find_picks(revs or ('HEAD',), since), opts, normalize_level)
    bad = [r for r in results if not r.ok(normalize_level)]
    if json_out:
        echo(json.dumps([r.to_json(normalize_level) for r in results], indent=2))
    else:
        for r in results:
            if not verbose and r.ok(normalize_level) and r.cls != 'missing':
                continue
            p = r.pick
            src = (p.source_sha or p.source)[:10]
            echo(f"{p.commit[:10]} {r.cls:<13} {src:<10} {p.subject}")
            if verbose or not r.ok(normalize_level):
                for f in r.files:
                    if f.status != 'equal':
                        echo(f"    {f.label:<13} {f.display_name}")
    err(f"{len(results)} pick(s): {summarize([r.cls for r in results], PICK_ORDER)}" if results else "No picks found")
    if bad:
        sys.exit(1)


PICK_ORDER = ['equal', 'equal@lines', 'equal@context', 'equal@ws', 'equal@tokens', 'differs', 'files-differ', 'missing']


def print_delta(result: DeltaResult, verbose: bool) -> None:
    (lf, la, ld), (rf, ra, rd) = result.sizes()
    (base_a, tip_a), (base_b, tip_b) = result.left_range, result.right_range
    echo(f"{result.label}")
    echo(f"  delta: {lf} files +{la}/-{ld} → {rf} files +{ra}/-{rd}")
    if result.absorbed:
        echo(f"  absorbed picks: {len(result.absorbed)}")
    labels = [f.label for f in result.files]
    if labels:
        echo(f"  {summarize(labels)}")
    for f in result.files:
        if verbose or f.status not in ('equal', 'absorbed', 'adopted'):
            extra = f" ({f.absorbed_lines}/{f.residual_lines} residual lines explained)" if f.absorbed_lines and f.label == 'differs' else ''
            echo(f"    {f.label:<13} {f.display_name}{extra}")
    if not result.ok():
        echo(f"  git-didi patch {base_a[:10]}..{tip_a[:10]} {base_b[:10]}..{tip_b[:10]}")


def delta_opts(func):
    """Options shared by `delta` and `merge`."""
    for decorator in reversed([
        opt('-C', '--find-copies', type=str, metavar='[<n>]', help='Detect copies as well as renames (similarity threshold, e.g., 50% or 0.5)'),
        json_flag,
        opt('-M', '--find-renames', type=str, metavar='[<n>]', help='Detect renames (similarity threshold, e.g., 50% or 0.5)'),
        normalize_opt,
        opt('-U', '--unified', type=int, default=3, help='Number of context lines in compared patches (default: 3)'),
        flag('-v', '--verbose', help='List every file, not just ones that differ'),
        flag('-w', '--ignore-whitespace', help='Pass -w to git diff commands to ignore whitespace'),
    ]):
        func = decorator(func)
    return func


def report_deltas(results: list[DeltaResult], json_out: bool, verbose: bool) -> None:
    if json_out:
        echo(json.dumps([r.to_json() for r in results], indent=2))
    else:
        for i, r in enumerate(results):
            if i:
                echo()
            print_delta(r, verbose)
    bad = [r for r in results if not r.ok()]
    if bad:
        err(f"{len(bad)} of {len(results)} delta(s) differ")
        sys.exit(1)


@cli.command()
@delta_opts
@opt('-a', '--at', 'at', multiple=True, help='Reflog selector for both branches (`CHILD@{AT}`), e.g. a date; pass once or twice (default for the 2nd: current tips)')
@arg('refs', nargs=-1, required=True)
def delta(
    find_copies: str,
    json_out: bool,
    find_renames: str,
    normalize_level: str,
    unified: int,
    verbose: bool,
    ignore_whitespace: bool,
    at: tuple[str, ...],
    refs: tuple[str, ...],
) -> None:
    """Compare a child branch's own delta at two points.

    The own delta at a point is `merge-base(CHILD, PARENT)..CHILD`.

    Files are classified as equal / equal@<level> (see "patch --help"), absorbed
    (the residual is fully explained by commits that entered the base and
    duplicate the child's own commits, by `-x` trailer or patch-id), adopted
    (lines the delta lost are now in the base, without a matching pick),
    differs, left-only, or right-only. Exits 1 if any file differs (at the -n level), 2
    on error.

    \b
    Forms:
        git-didi delta CHILD PARENT --at A [--at B]
        git-didi delta CHILD_A PARENT_A CHILD_B PARENT_B
    """
    if len(refs) == 4:
        if at:
            err("--at doesn't apply with 4 refs")
            sys.exit(2)
        points = [(refs[0], refs[1]), (refs[2], refs[3])]
    elif len(refs) == 2:
        if not 1 <= len(at) <= 2:
            err("Pass --at once or twice with CHILD PARENT")
            sys.exit(2)
        child, parent = refs
        points = [
            (f'{child}@{{{a}}}', f'{parent}@{{{a}}}') if a is not None else (child, parent)
            for a in (at[0], at[1] if len(at) > 1 else None)
        ]
    else:
        err("Expected CHILD PARENT (with --at), or CHILD_A PARENT_A CHILD_B PARENT_B")
        sys.exit(2)
    (child_a, parent_a), (child_b, parent_b) = points
    tip_a, tip_b = rev_parse(child_a), rev_parse(child_b)
    before = (merge_base(tip_a, rev_parse(parent_a)), tip_a)
    after = (merge_base(tip_b, rev_parse(parent_b)), tip_b)
    opts = DiffOpts(ignore_whitespace, unified, find_renames, find_copies)
    result = compare_delta(f'{child_a} vs {parent_a} → {child_b} vs {parent_b}', before, after, opts, normalize_level)
    report_deltas([result], json_out, verbose)


@cli.command()
@delta_opts
@arg('merges', nargs=-1, required=True)
def merge(
    find_copies: str,
    json_out: bool,
    find_renames: str,
    normalize_level: str,
    unified: int,
    verbose: bool,
    ignore_whitespace: bool,
    merges: tuple[str, ...],
) -> None:
    """Check that merges preserve the first parent's own delta.

    For each merge M, compares `merge-base(M^1, M^2)..M^1` with `M^2..M`. A
    range arg (e.g. `gcs~20..gcs`) expands to its first-parent merges.

    Files are classified as equal / equal@<level> (see "patch --help"), absorbed
    (the residual is fully explained by commits that entered the base and
    duplicate the child's own commits, by `-x` trailer or patch-id), adopted
    (lines the delta lost are now in the base, without a matching pick),
    differs, left-only, or right-only. Exits 1 if any file differs (at the -n level), 2
    on error.

    \b
    Examples:
        git-didi merge a96bd9b3
        git-didi merge -v gcs~20..gcs
    """
    opts = DiffOpts(ignore_whitespace, unified, find_renames, find_copies)
    shas = expand_merges(merges)
    if not shas:
        err("No merges found")
        sys.exit(2)
    results = []
    for sha in shas:
        result = merge_delta(sha, opts, normalize_level)
        subject = git('log', '-1', '--format=%s', sha).strip()
        result.label = f'{sha[:10]} {subject}'
        results.append(result)
    report_deltas(results, json_out, verbose)


@cli.command()
@opt('-c', '--config', default='didi.yml', help='Lanes config: `didi.yml` (`lanes: {name: {parent, …}}`) or a `/cp` `.claude/cp.yml` (default: didi.yml)')
@opt('-C', '--find-copies', type=str, metavar='[<n>]', help='Detect copies as well as renames (similarity threshold, e.g., 50% or 0.5)')
@opt('-j', '--jobs', type=int, default=8, help='Merges to check in parallel (default: 8)')
@opt('-M', '--find-renames', type=str, metavar='[<n>]', help='Detect renames (similarity threshold, e.g., 50% or 0.5)')
@normalize_opt
@opt('-o', '--output', default='-', help='Output path (default: stdout)')
@opt('-p', '--previous', help='Previous report: reuse its pick/merge records for unchanged commits (when options match)')
@opt('-s', '--since', help='Only commits since this date (`git log --since`; default: the config\'s `since`)')
@opt('-U', '--unified', type=int, default=3, help='Number of context lines in compared patches (default: 3)')
@flag('-w', '--ignore-whitespace', help='Pass -w to git diff commands to ignore whitespace')
def report(
    config: str,
    find_copies: str,
    jobs: int,
    find_renames: str,
    normalize_level: str,
    output: str,
    previous: str,
    since: str,
    unified: int,
    ignore_whitespace: bool,
) -> None:
    """Write a JSON report of lanes, segments, picks, and merges, for the web viewer.

    Lanes come from the config; each lane's first-parent history (since -s)
    becomes segments: runs of its own commits, merges from its parent or peers,
    and groups of cherry-picks. Every pick gets its `picks` class, and every
    parent merge its `merge` own-delta check (class = its worst file). Non-equal
    files carry their patch pair, so the viewer needs no git. Schema:
    www/schema.json.

    \b
    Examples:
        git-didi report -s 2026-09-28 -o report.json
        git-didi report -p report.json -o report.json   # incremental
    """
    try:
        cfg = load_config(config)
    except (OSError, ValueError) as e:
        err(str(e))
        sys.exit(2)
    prev = None
    if previous:
        try:
            with open(previous) as f:
                prev = json.load(f)
        except FileNotFoundError:
            err(f"No previous report at {previous}; building from scratch")
    opts = DiffOpts(ignore_whitespace, unified, find_renames, find_copies)
    try:
        data = build_report(cfg, since, opts, normalize_level, prev, jobs, log=err)
    except ValueError as e:
        err(str(e))
        sys.exit(2)
    text = json.dumps(data, separators=(',', ':')) + '\n'
    if output == '-':
        sys.stdout.write(text)
    else:
        with open(output, 'w') as f:
            f.write(text)
        err(f"Wrote {output}: {len(data['lanes'])} lanes, {len(data['picks'])} picks, {len(data['merges'])} merges")


@cli.command()
@opt('-c', '--config', help='Lanes config for the report view (default: didi.yml, if present; else only range comparisons)')
@opt('-C', '--find-copies', type=str, metavar='[<n>]', help='Detect copies as well as renames (similarity threshold, e.g., 50% or 0.5)')
@opt('-H', '--host', default='127.0.0.1', help='Interface to bind (default: 127.0.0.1)')
@opt('-j', '--jobs', type=int, default=8, help='Merges to check in parallel when building the report (default: 8)')
@opt('-M', '--find-renames', type=str, metavar='[<n>]', help='Detect renames (similarity threshold, e.g., 50% or 0.5)')
@normalize_opt
@opt('-p', '--port', type=int, default=6093, help='Port (default: 6093)')
@opt('-s', '--since', help="Report: only commits since this date (default: the config's `since`)")
@opt('-U', '--unified', type=int, default=3, help='Number of context lines in compared patches (default: 3)')
@flag('-w', '--ignore-whitespace', help='Pass -w to git diff commands to ignore whitespace')
@opt('-W', '--www', help='Built viewer directory (default: the packaged one, else www/dist in a source checkout)')
def serve(
    config: str,
    find_copies: str,
    host: str,
    jobs: int,
    find_renames: str,
    normalize_level: str,
    port: int,
    since: str,
    unified: int,
    ignore_whitespace: bool,
    www: str,
) -> None:
    """Serve the web viewer for the current repo.

    With a lanes config, the viewer loads this repo's report (built on first
    view, cached at $GIT_DIR/didi/report.json, rebuilt incrementally from the
    viewer's refresh button). Either way, its Compare view diffs any two ranges
    (`patch A..B C..D`), with every non-equal file's diff of diffs.

    \b
    Examples:
        git-didi serve -c didi.yml
        git-didi serve              # range comparisons only (no didi.yml)
    """
    from pathlib import Path
    from .serve import find_www, serve as run_server
    try:
        www_dir = find_www(www)
    except FileNotFoundError as e:
        err(str(e))
        sys.exit(2)
    cfg = None
    if config or Path('didi.yml').is_file():
        try:
            cfg = load_config(config or 'didi.yml')
        except (OSError, ValueError) as e:
            err(str(e))
            sys.exit(2)
    opts = DiffOpts(ignore_whitespace, unified, find_renames, find_copies)
    run_server(host, port, www_dir, cfg, opts, normalize_level, since, jobs, log=err)


if __name__ == '__main__':
    cli()
