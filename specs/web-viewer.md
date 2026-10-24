# A web viewer for git-didi: branch lanes, pick/merge audits, diff-of-diffs in color

**Status:** draft, 2026-10-24, from the disky session. Nothing implemented. Builds on [`done/delta-invariance-audit.md`][audit] (`--json`, `picks`, `merge`/`delta`, normalization levels).

## Why

- The disky deck's branch slide ([`disky-deep-dive` `react/GitDag.tsx`][gitdag], data from `scripts/branches.py`) got attention: lanes per long-lived branch, daily commit counts, merges and `-x` picks drawn as arrows. It shows that changes flow, but not whether they flowed **faithfully**. git-didi can answer that (`picks`, `merge`, `delta`), but its output is terminal text.
- A diff of diffs has two nested axes: outer (which side has the line) and inner (`+`/`-`/context within each patch). In 256 colors, in a TUI, that never read well. HTML gets full color, a gutter per axis, token highlights, folding, and links.
- The interesting picture is mostly **sameness**: long runs of picks and merges whose didi is empty, broken by a few intrinsic or semantic differences. A view should compress the sameness and spend its pixels on the differences.

## What it shows

### 1. Lanes (the slide's DAG, audited)

The [GitDag] layout, generalized: one lane per branch, from a config (below).
- Each day's dot is the commit count on that branch, with picks received as wedges, as on the slide.
- **New: every merge arrow and pick wedge carries its didi class.** A pick is `equal`, `equal@lines`, `equal@context`, `equal@tokens`, `differs` or `files-differ`. A merge is the worst file class of its own-delta check: `equal`, `absorbed`, `adopted`, `equal@<level>` or `differs`.
  - Equal is drawn quiet (the lane's colour, thin).
  - Non-equal is drawn loud (an outlined arrow, a badge with the level).
- Clicking any dot, arrow or wedge opens its detail (§3).

### 2. Pair tracks: a child's own delta against its parent, over time

For one (child, parent) pair, e.g. `gcs` ↔ `cloud`, two columns of commits aligned by provenance: a pick sits on the same row as its source, by `-x` trailer, else by `git patch-id --stable`.
- **Runs of equal pairs collapse into one band**, e.g. "37 picks, empty didi (31 equal, 6 equal@lines)". It expands on click.
- **Non-equal pairs stay open**, each showing its residual at the first level where it's small: the `tokens` edit script when it's equal at `tokens`, else the diff of diffs.
- **Unpaired commits are the child's intrinsic delta.** They're grouped by surface (paths, from the config) so "config, migrations, deployment glue" reads as intended, and a commit touching a parity surface is flagged.
- **Above the tracks:** the own delta's size over time (`+/-` lines, from `delta --json` at each merge), with each merge's class marked. That's the "intrinsic delta only goes down" trend, as a chart.

### 3. Detail: a diff of diffs in HTML

For a pick, a merge, or any two ranges (`patch A..B C..D`):
- one row per line, with two gutters: outer (left / right / both) and inner (`+` / `-` / context);
- colour is the pair: a background per inner sign, a tint per outer side, all CSS tokens with both themes;
- `tokens`-level residuals inline: the changed tokens highlighted inside the line, as the `Treemap.tsx` case in [audit] (`, stagedRings = true` inserted on both sides, on lines the parent also changed);
- files folded by class, with equal files collapsed into a count;
- every commit, file and line links to GitHub (repo URLs from the config);
- the `git-didi …` command that reproduces it, copyable.

## Data: `git-didi report`

One JSON bundle per repo, produced offline, so the viewer can be static.

```bash
git-didi report -c didi.yml -s 2026-09-28 -o report.json
```

- **Config** (`didi.yml`, or `.claude/cp.yml`'s pairs): lanes `{name, ref, parent, color, label, repo_url, surfaces: {intrinsic: [globs], parity: [globs]}}`. Ref aliases cover renames (disky's `cw-s3` → `cw`). It holds what `branches.py`'s `LONG` / `RENAMED` hold today.
- **Contents:**
  - lanes;
  - per-lane first-parent segments (runs, merges, picks with dates: `branches.py`'s `segments`, moved here);
  - every pick's `picks --json` record;
  - every merge's `merge --json` record;
  - per-pair own-delta sizes at each merge;
  - for **non-equal** items only, the normalized per-file patch pairs, so §3 renders without git.

  Equal items carry no patches, which keeps the bundle small: disky's ~400 picks are mostly equal.
- **Incremental:** `-p report.json` reuses records for unchanged SHAs; picks and merges are immutable once made. The cost grows with new commits, not with history.
- **Contract:** JSON Schema in `www/schema.json`. The deck's `branches.ts` reads the same lanes and segments, so the slide and the viewer can't drift.

## The viewer: `www/` in this repo

- Vite + React + TS, static, with no server for a report.
  - `?r=<url>` loads a report (CORS-readable: GH Pages, R2, raw GitHub), or drag-and-drop a local file.
  - `?pair=gcs,cloud`, `?item=<sha>`: deep links via `use-prms`.
  - The lanes component is ported from the deck's `GitDag.tsx`.
- **`git-didi serve [-c didi.yml]`** runs it locally against the current repo: it builds the report on demand, and adds one local endpoint for arbitrary `patch A..B C..D`. §3 then works for any ranges, not only the report's items. That's the "browse / diff / didi any branches" mode, and it's local only, since it needs the repo.
- Published at `runsascoded.github.io/git-didi`. A project hosts its own report anywhere CORS-readable and links `…/git-didi/?r=<its url>`.

## disky as the first consumer

- disky's `specs/branch-harmony-audit.md` (draft, local to `~/c/disky`, not yet committed) already plans `scripts/branch-harmony` on `cloud`, running `merge`/`picks` per pair after every merge and pick. It would run `git-didi report -p` and upload `report.json` to a public R2 key. The repo is public, so branch metadata is too. It's refreshed by the `/cp` skill's last step and a nightly GHA.
- **The deck:**
  - the branch slide gets the per-arrow classes (§1) from the same report;
  - a new slide or appendix, "disky – picks and merges, audited", shows the `gcs` ↔ `cloud` pair tracks (§2) with one expanded non-equal item (§3), and links to the live viewer.
- **Not in disky's `site/`.** That's the storage-usage product. Every deployment ships its full feature set (parity by default), so a dev-process page there would reach gcs.oa.dev, cw.oa.dev, etc., and couple the product to its own repo's history. The viewer belongs to git-didi; disky publishes data for it.

## Phases

1. `report` (lanes + segments + picks + merges, incremental) with a schema and tests on the fixture repo. The deck switches `branches.py` → `git-didi report`.
2. `www/`: lanes with classes (§1) and the detail view (§3) for report items. GH Pages.
3. Pair tracks (§2) and the own-delta trend.
4. `serve`, for arbitrary ranges locally.
5. disky: `scripts/branch-harmony` publishes the report; the deck slide or appendix.

## Open

- Pairing beyond trailers: `patch-id` pairs re-applied picks that lost their trailer. Should `tokens`-equal commits pair too (adapted picks without `-x`)? Probably as "suggested pairs", shown dashed.
- AST level (tree-sitter), deferred in [audit]: the detail view is where it would pay off (e.g. "added parameter `x`" instead of a token script).
- Whether the deck should import the viewer's lanes component (a `pds`-managed dep) or keep its own copy reading the shared schema.

[audit]: done/delta-invariance-audit.md
[gitdag]: https://github.com/Open-Athena/ddd/blob/main/react/GitDag.tsx
[GitDag]: https://github.com/Open-Athena/ddd/blob/main/react/GitDag.tsx
