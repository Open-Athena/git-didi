# git-didi

[![git-didi](https://img.shields.io/pypi/v/git-didi?label=git-didi)](https://pypi.org/project/git-didi/)

Compare diffs between two git ranges - a "diff of diffs" tool.

> **Demo Branches**: See [test scenarios](docs/test-strategy.md#proposed-test-branch-structure) for examples you can try. Test scenario 01 is live in this repo!

This tool is particularly useful for verifying rebases and merges, especially when complex conflict resolution was involved. It helps ensure that the actual changes in your branch remain approximately the same before and after rebasing onto a new upstream.

## Installation

```bash
pip install git-didi
```

Or with `uv`:

```bash
uv tool install git-didi
```

## Usage

### Common use case - checking a rebase

After fetching and rebasing your branch onto main:

```bash
git fetch
git rebase main
```

You can verify the rebase preserved your changes:

```bash
git-didi patch main@{1}..branch@{1} main..branch
```

This compares:
- **Left side**: Your changes before the rebase (`main@{1}..branch@{1}`)
- **Right side**: Your changes after the rebase (`main..branch`)

The `@{1}` syntax refers to the previous position in the reflog. If both refs moved exactly once during the rebase, `@{1}` will work. If you've done multiple operations, you may need `@{2}`, `@{3}`, etc. Use `git reflog` to find the right positions.

### Commands

#### `stat` - Compare diff stats

Compare `git diff --stat` output between two refspecs:

```bash
git-didi stat main..feature upstream/main..feature
```

Shows only the files where the diff statistics differ.

#### `patch` - Compare patches file-by-file

Compare patches between two refspecs, showing differences for each file:

```bash
git-didi patch main..feature upstream/main..feature
```

This shows a "diff of diffs" with sophisticated coloring to distinguish:
- Changes in the outer diff (what changed between the two versions)
- Changes in the inner diffs (the actual patches)

Options:
- `-n LEVEL` / `--normalize LEVEL`: Normalization level at which patches count as equal (see [Normalization levels](#normalization-levels); default: `index`)
- `-j` / `--json`: Print one JSON record per file (`{path, status, level, left_lines, right_lines}`) instead of diffs
- `-U N` / `--unified N`: Set context lines (default: 3)
- `-q` / `--quiet`: Only list files with differences
- `-w` / `--ignore-whitespace`: Ignore whitespace changes
- `-M[n]` / `--find-renames[=n]`: Detect renames
- `-C[n]` / `--find-copies[=n]`: Detect copies
- `--color {auto,always,never}`: Control colored output
- `--pager {auto,always,never}`: Control pager usage

Each file is also classified by the least-lenient level at which its two patches are equal, and a summary goes to stderr, e.g. `12 equal, 3 equal@lines, 1 equal@context, 2 differs`.

#### Normalization levels

Levels are cumulative; each ignores everything the previous ones do:

| level | ignores |
|---|---|
| `none` | nothing |
| `index` (default) | `index <sha>..<sha>` lines |
| `lines` | hunk-header line numbers (`@@ -a,b +c,d @@ ctx` → `@@ ctx`) |
| `context` | context lines and hunk boundaries (compares each file's ordered `+`/`-` lines) |
| `ws` | whitespace within changed lines |
| `tokens` | whole lines: each block of `-`/`+` lines becomes a token-level edit script, e.g. both `f(a, b)` → `f(a, b, x=1)` and `f(a, b, c)` → `f(a, b, c, x=1)` are `insert: , x = 1` |

#### `picks` - Check cherry-pick fidelity

For each commit with a `(cherry picked from commit X)` trailer, compare `X`'s patch with the pick's:

```bash
git-didi picks main..feature
git-didi picks -s 2026-09-28 gcs cw hf   # `git log` args; default HEAD
```

Each pick is `equal`, `equal@<level>`, `differs`, `files-differ` (different file sets, e.g. an adapted pick), or `missing` (source not in the repo). Lists the picks that aren't equal at the `-n` level (`-v`: all), plus a summary.

#### `merge` / `delta` - Check that a branch's own delta survives merges

```bash
git-didi merge a96bd9b3          # mb(M^1, M^2)..M^1  vs  M^2..M
git-didi merge gcs~20..gcs       # each first-parent merge in a range
git-didi delta gcs cloud --at 2026-10-01             # gcs's own delta then vs. now
git-didi delta CHILD_A PARENT_A CHILD_B PARENT_B     # explicit points
```

A child's own delta is `merge-base(child, parent)..child`. Merging the parent legitimately shrinks it when the parent already took some of the child's changes, so beyond `equal@<level>` files are:
- `absorbed`: the residual is fully explained by commits that entered the base and duplicate the child's own commits (by `-x` trailer, in either direction, or by `git patch-id --stable`)
- `adopted`: lines the delta lost are now in the base without a matching pick (e.g. the parent copied files in a squashed commit)
- `differs` / `left-only` / `right-only`: anything else

Prints each delta's size before and after, and a `git-didi patch …` command to inspect what differs.

#### `report` - Build a JSON bundle for the web viewer

```bash
git-didi report -c didi.yml -s 2026-09-28 -o report.json
git-didi report -p report.json -o report.json   # incremental: reuse records of unchanged picks/merges
```

`didi.yml` names the long-lived branches ("lanes") and their parents:

```yaml
since: 2026-09-28
repo_url: https://github.com/org/repo   # lanes' default
ignore_branches: [worktree-agent-*]     # never used to name a commit's branch
lanes:
  main: {label: base, color: '#5b5b5b'}
  deploy-a: {parent: main, color: '#4285f4', aliases: [old-name]}
```

(A `/cp` skill `.claude/cp.yml` also works: its first branch is the root, the rest its children.) The report holds each lane's first-parent history as segments (runs of own commits, merges from its parent or peers, groups of cherry-picks), every pick's `picks` record, and every parent merge's `merge` record (classed by its worst file). Files whose residual isn't explained by their label carry their diff of diffs, so the viewer needs no git. Schema: [`www/schema.json`](www/schema.json).

#### Web viewer

[`www/`](www) is a static viewer for reports (published at [didi.oa.dev][viewer]): lanes per branch with every merge and cherry-pick drawn by its class (quiet when equal, a halo when equal once normalized, a `≠` badge when it differs), a per-lane audit table, and each pick's or merge's diff of diffs in color (outer and inner gutters, the edit within each patch and the difference between the two highlighted at token level). Load a report with `?r=<CORS-readable URL>` (`&item=<sha>` opens one item), or drop a file on the page.

`git-didi serve` runs the viewer locally against the current repo, on [localhost:6093](http://localhost:6093): with a `didi.yml` (or `-c`), it builds the repo's report on first view (cached in `$GIT_DIR/didi/report.json`, refreshed incrementally from the viewer), and its Compare view diffs any two ranges (`?left=A..B&right=C..D`, like `git-didi patch`, with every non-equal file's diff of diffs).

```bash
git-didi serve -c didi.yml
cd www && pnpm install && pnpm dev   # viewer dev server (port 6092); proxies /api to a running `git-didi serve`
cd www && pnpm run deploy            # build and `wrangler deploy` to didi.oa.dev (needs `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`)
```

#### Exit codes

`patch`, `stat`, `picks`, `merge`, and `delta` exit 1 when they find differences (at the `-n` level), 2 on errors, 0 otherwise.

#### `commits` - Compare commits

Compare individual commits between two refspecs:

```bash
git-didi commits main..feature upstream/main..feature
```

First verifies that commits correspond (same count and messages), then shows per-commit differences.

#### `swatches` - Display color palette

Show color swatches demonstrating the diff-of-diffs coloring scheme:

```bash
git-didi swatches --color=always
```

### Filtering by path

All commands support filtering to specific paths:

```bash
git-didi patch main..feature upstream/main..feature -- src/
git-didi stat main..feature upstream/main..feature -- "*.py"
```

## Git Aliases

You can add these to your `~/.gitconfig` for convenient access:

```ini
[alias]
    didi = !git-didi
    gdds = !git-didi stat
    gddp = !git-didi patch
    gddc = !git-didi commits
```

Then use:

```bash
git didi patch main@{1}..branch@{1} main..branch
git gddp main..feature upstream/main..feature
```

## How it works

The tool automatically filters out spurious differences like git index SHAs that change even when the actual patch content is identical (and `-n` can ignore more; see [Normalization levels](#normalization-levels)). This makes it easy to verify that a rebase or cherry-pick truly preserved your changes without introducing unexpected modifications.

When comparing patches, it uses a sophisticated 256-color palette to make nested diffs easy to read:
- Bright backgrounds for added/removed lines within the outer diff
- Dark backgrounds for context lines
- Mixed colors for lines that changed type (+ to - or vice versa)

## Try it yourself - Test Scenarios

This repo includes test branches demonstrating various rebase/merge scenarios. Try:

```bash
# Clone this repo
git clone https://github.com/ryan-williams/git-didi.git
cd git-didi

# Example: Clean rebase with disjoint changes
# Alice adds priority field, Bob adds JSON format - no conflicts!
git-didi patch tests/01-clean-disjoint/base..tests/01-clean-disjoint/alice \
               tests/01-clean-disjoint/bob..tests/01-clean-disjoint/alice-rebased-on-bob
# Output: "No differences in patches" ✓

# See what each developer changed
git log --oneline tests/01-clean-disjoint/base..tests/01-clean-disjoint/alice
git log --oneline tests/01-clean-disjoint/base..tests/01-clean-disjoint/bob

# Try the other direction
git-didi patch tests/01-clean-disjoint/base..tests/01-clean-disjoint/bob \
               tests/01-clean-disjoint/alice..tests/01-clean-disjoint/bob-rebased-on-alice

# Or verify a merge commit preserved both changes
git-didi patch tests/01-clean-disjoint/base..tests/01-clean-disjoint/alice \
               tests/01-clean-disjoint/merged^1..tests/01-clean-disjoint/merged
```

See [Test Strategy](docs/test-strategy.md) for details on the test scenarios and how to generate more.

## Development

```bash
# Clone and setup
git clone https://github.com/ryan-williams/git-didi.git
cd git-didi

# Setup with uv
uv sync --extra test

# Run tests
pytest tests/ -v

# Generate additional test scenarios
./scripts/generate-test-scenario.py 01-clean-disjoint

# Install locally
uv pip install -e .
```

## License

MIT License - see [LICENSE] for details.

[LICENSE]: LICENSE
[viewer]: https://didi.oa.dev
