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
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from subprocess import run

from click import Choice, echo, group, style
from utz import err
from utz.cli import arg, flag, opt

from .color import should_use_color
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
from .pager import Pager


# Common option decorators
color_opt = opt('-c', '--color', type=Choice(['auto', 'always', 'never']), default='auto', help='When to use colored output (default: auto)')
pager_opt = opt('--pager', type=Choice(['auto', 'always', 'never']), default='auto', help='When to use pager (default: auto)')
find_copies_opt = opt('-C', '--find-copies', type=str, metavar='[<n>]', help='Detect copies as well as renames (similarity threshold, e.g., 50% or 0.5)')
find_renames_opt = opt('-M', '--find-renames', type=str, metavar='[<n>]', help='Detect renames (similarity threshold, e.g., 50% or 0.5)')
ignore_whitespace_flag = flag('-w', '--ignore-whitespace', help='Pass -w to git diff commands to ignore whitespace')


def common_opts(func):
    """Apply common options to all commands."""
    func = color_opt(func)
    func = pager_opt(func)
    func = find_copies_opt(func)
    func = find_renames_opt(func)
    func = ignore_whitespace_flag(func)
    return func


@group()
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
            sys.exit(1)
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
            sys.exit(1)

        cmd2 = build_diff_cmd(ignore_whitespace, find_renames, find_copies, follow=use_follow)
        cmd2.extend(['--numstat', refspec2])
        if paths:
            cmd2.extend(['--', *paths])
        result2 = run(cmd2, capture_output=True, text=True)
        if result2.returncode != 0:
            err(f"Error getting diff for {refspec2}: {result2.stderr}")
            sys.exit(1)

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


@cli.command()
@common_opts
@opt('-p', '--path', multiple=True, help='Paths to compare (enables pipeline mode; supports @file and - for stdin)')
@flag('-r', '--rebase', help='Rebase mode: auto-detect refspecs via git ours-refspec/theirs-refspec')
@opt('-U', '--unified', type=int, default=3, help='Number of context lines to show (default: 3)')
@flag('-q', '--quiet', help='Only show files with differences')
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
            sys.exit(1)
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
            sys.exit(1)
    else:
        # Backward compatible: remaining args are paths, no pipeline
        pipeline_cmds = []
        paths = remaining_args

    # Determine color BEFORE pager redirects stdout
    use_color = should_use_color(color)

    with Pager(pager):
        # Pipeline mode: compare pipeline-processed diffs
        if pipeline_cmds:
            different_files = []
            for filepath in paths:
                diff1 = get_file_diff_with_pipeline(
                    refspec1, filepath, pipeline_cmds,
                    ignore_whitespace=ignore_whitespace,
                    unified=unified,
                )
                diff2 = get_file_diff_with_pipeline(
                    refspec2, filepath, pipeline_cmds,
                    ignore_whitespace=ignore_whitespace,
                    unified=unified,
                )

                if diff1 != diff2:
                    different_files.append(filepath)
                    if not quiet:
                        echo(style(f"\n{'='*60}", fg='blue') if use_color else f"\n{'='*60}")
                        echo(style(f"File: {filepath}", fg='yellow', bold=True) if use_color else f"File: {filepath}")
                        echo(style(f"{'='*60}", fg='blue') if use_color else f"{'='*60}")

                        # Show the diff of diffs for this file
                        from_label = f'{filepath} in {refspec1}'
                        to_label = f'{filepath} in {refspec2}'
                        diff_lines = list(difflib.unified_diff(
                            diff1.splitlines(),
                            diff2.splitlines(),
                            fromfile=from_label,
                            tofile=to_label,
                            lineterm=''
                        ))

                        for line in diff_lines:
                            if use_color:
                                if line.startswith('+') and not line.startswith('+++'):
                                    echo(style(line, fg='green'), color=True)
                                elif line.startswith('-') and not line.startswith('---'):
                                    echo(style(line, fg='red'), color=True)
                                elif line.startswith('@@'):
                                    echo(style(line, fg='cyan'), color=True)
                                else:
                                    echo(line)
                            else:
                                echo(line)

            if quiet and different_files:
                echo(style("\nFiles with different patches:", fg='yellow', bold=True) if use_color else "\nFiles with different patches:")
                for f in different_files:
                    echo(f"  {f}")

            if not different_files:
                err("No differences in patches")
            else:
                err(f"\n{len(different_files)} file(s) have different patches")
            return

        # Non-pipeline mode: original behavior
        # Compute upstream range to detect renames
        # E.g., if comparing A..B vs C..D, look at A..C for upstream changes
        upstream_range = compute_upstream_range(refspec1, refspec2)
        rename_map = {}
        if upstream_range:
            rename_map = get_rename_mapping(upstream_range, find_renames, find_copies)
            if rename_map:
                err(f"Detected {len(rename_map)} rename(s) in upstream ({upstream_range})")

        # Get list of changed files in both refspecs
        files1 = get_changed_files(refspec1, paths)
        files2 = get_changed_files(refspec2, paths)

        # Apply rename mapping to files1
        # If a file was renamed in upstream, we need to look for it under the new name in refspec2
        files1_mapped = []
        for f in files1:
            if f in rename_map:
                files1_mapped.append((f, rename_map[f]))  # (old_name, new_name)
            else:
                files1_mapped.append((f, f))  # (name, name)

        # Build set of all file names to compare
        all_files_to_compare = []
        for old_name, new_name in files1_mapped:
            all_files_to_compare.append((old_name, new_name))

        # Also check files that only appear in files2
        files1_new_names = {new_name for _, new_name in files1_mapped}
        for f2 in files2:
            if f2 not in files1_new_names:
                all_files_to_compare.append((f2, f2))

        # Fetch all diffs in parallel
        def fetch_diffs(old_path, new_path):
            diff1 = get_file_diff(refspec1, old_path, ignore_whitespace, unified, find_renames, find_copies)
            diff2 = get_file_diff(refspec2, new_path, ignore_whitespace, unified, find_renames, find_copies)
            return (old_path, new_path), diff1, diff2

        file_diffs = {}
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(fetch_diffs, old_path, new_path): (old_path, new_path)
                      for old_path, new_path in all_files_to_compare}
            for future in as_completed(futures):
                file_pair, diff1, diff2 = future.result()
                file_diffs[file_pair] = (diff1, diff2)

        # Process results in order
        different_files = []
        for old_path, new_path in all_files_to_compare:
            diff1, diff2 = file_diffs[(old_path, new_path)]

            # Normalize diffs to ignore index SHAs and map paths
            norm_diff1 = normalize_diff(diff1, rename_map)
            norm_diff2 = normalize_diff(diff2)

            if norm_diff1 != norm_diff2:
                # Display name: show rename if applicable
                display_name = f"{old_path} → {new_path}" if old_path != new_path else old_path
                different_files.append(display_name)
                if not quiet:
                    echo(style(f"\n{'='*60}", fg='blue') if use_color else f"\n{'='*60}")
                    echo(style(f"File: {display_name}", fg='yellow', bold=True) if use_color else f"File: {display_name}")
                    echo(style(f"{'='*60}", fg='blue') if use_color else f"{'='*60}")

                    # Show the diff of diffs for this file
                    from_label = f'{old_path} in {refspec1}'
                    to_label = f'{new_path} in {refspec2}'
                    diff_lines = list(difflib.unified_diff(
                        diff1.splitlines(),
                        diff2.splitlines(),
                        fromfile=from_label,
                        tofile=to_label,
                        lineterm=''
                    ))

                    for line in diff_lines:
                        if use_color:
                            # Handle unified diff headers from outer diff first (---, +++, @@)
                            # These are lines from the outer diff, not nested patterns
                            if line.startswith('---') and not line.startswith('----'):
                                # Real outer diff header - red fg
                                echo(style(line, fg='red'), color=True)
                                continue
                            elif line.startswith('+++') and not line.startswith('++++'):
                                # Real outer diff header - green fg
                                echo(style(line, fg='green'), color=True)
                                continue
                            elif line.startswith('@@') and not (line.startswith('-@@') or line.startswith('+@@') or line.startswith(' @@')):
                                # Hunk header from outer diff (not nested)
                                echo(style(line, fg='cyan'), color=True)
                                continue
                            elif line.startswith('-@@') or line.startswith('-index ') or line.startswith('-diff ') or line.startswith('----') or line.startswith('-+++'):
                                # Nested metadata in removed section - red fg only
                                echo(style(line, fg='red'), color=True)
                                continue
                            elif line.startswith('+@@') or line.startswith('+index ') or line.startswith('+diff ') or line.startswith('++++') or line.startswith('+---'):
                                # Nested metadata in added section - green fg only
                                echo(style(line, fg='green'), color=True)
                                continue

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
                        else:
                            echo(line)

        if quiet and different_files:
            echo(style("\nFiles with different patches:", fg='yellow', bold=True) if use_color else "\nFiles with different patches:")
            for f in different_files:
                echo(f"  {f}")

        if not different_files:
            err("No differences in patches")
        else:
            err(f"\n{len(different_files)} file(s) have different patches")


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
            sys.exit(1)
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


if __name__ == '__main__':
    cli()
