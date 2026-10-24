# A web viewer for git-didi: branch lanes, pick/merge audits, diff-of-diffs in color

**Status:** phases 1 (`report`), 2 (`www/` lanes + detail) and 3 (pair tracks + own-delta trend) implemented 2026-10-23 (see [Implementation notes](#implementation-notes)); phases 4–5 pending. Drafted 2026-10-24, from the disky session. Builds on [`done/delta-invariance-audit.md`][audit] (`--json`, `picks`, `merge`/`delta`, normalization levels).

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
- **Demo at `didi.oa.dev`** (leaning OA, since most of the driving work is OA's disky; `didi.rbw.sh` the alternative): a Cloudflare Pages site serving the viewer, with disky's report as its default `?r=`. Any other project hosts its own report anywhere CORS-readable and links `didi.oa.dev/?r=<its url>`.
  - **Setup:** a Pages project + the `didi` CNAME in OA's Cloudflare account, as Pulumi beside disky's `cf/` stacks, plus the report's public R2 key. The apply needs Ryan's go, as all infra does.

## disky as the first consumer

- disky's `specs/branch-harmony-audit.md` (draft, local to `~/c/disky`, not yet committed) already plans `scripts/branch-harmony` on `cloud`, running `merge`/`picks` per pair after every merge and pick. It would run `git-didi report -p` and upload `report.json` to the public R2 key `didi.oa.dev` reads. The repo is public, so branch metadata is too. It's refreshed by the `/cp` skill's last step and a nightly GHA.
- **The deck:**
  - the branch slide gets the per-arrow classes (§1) from the same report;
  - a new slide or appendix, "disky – picks and merges, audited", shows the `gcs` ↔ `cloud` pair tracks (§2) with one expanded non-equal item (§3), and links to `didi.oa.dev`.
- **Not in disky's `site/`.** That's the storage-usage product. Every deployment ships its full feature set (parity by default), so a dev-process page there would reach gcs.oa.dev, cw.oa.dev, etc., and couple the product to its own repo's history. The viewer belongs to git-didi; disky publishes data for it.

## Phases

1. `report` (lanes + segments + picks + merges, incremental) with a schema and tests on the fixture repo. The deck switches `branches.py` → `git-didi report`.
2. `www/`: lanes with classes (§1) and the detail view (§3) for report items, deployed to `didi.oa.dev` with disky's report.
3. Pair tracks (§2) and the own-delta trend.
4. `serve`, for arbitrary ranges locally.
5. disky: `scripts/branch-harmony` publishes the report; the deck slide or appendix.

## Open

- Pairing beyond trailers: `patch-id` pairs re-applied picks that lost their trailer. Should `tokens`-equal commits pair too (adapted picks without `-x`)? Probably as "suggested pairs", shown dashed.
- AST level (tree-sitter), deferred in [audit]: the detail view is where it would pay off (e.g. "added parameter `x`" instead of a token script).
- Whether the deck should import the viewer's lanes component (a `pds`-managed dep) or keep its own copy reading the shared schema.

## Implementation notes

### Phase 1: `report` (`src/didi/report.py`, `tests/test_report.py`, `www/schema.json`)

- **Config**: `didi.yml` is `lanes: {name: {parent, label, color, repo_url, aliases, surfaces}}` plus top-level `since`, `repo_url` (lanes' default) and `ignore_branches` (globs, e.g. `worktree-agent-*`, never used to name a commit's branch). A lane's `ref` is its name if that branch exists, else its first existing alias (`branches.py`'s `RENAMED`). A `/cp` `.claude/cp.yml` also loads; it has no parents, so its first branch is the root and the rest are children.
- **Segments**: `branches.py`'s `segments` ported, generic over lanes. A commit's lane is a root that reaches it, else the deepest lane whose first-parent spine (then own set) has it. Kinds: `run` (with feature merges folded in), `merge` (`from_kind` `parent` or `peer`), `cp` (consecutive picks grouped by source lane; a source outside every lane names a containing branch, `dangling`, or `missing`). `-s` bounds every lane's spine, not just the root's. Picks are the ones on a lane's first-parent spine (as in `branches.py`'s segments); picks arriving via feature merges aren't audited. Deck-specific parts (milestones, deploy pointers, feature-branch table, `[tag]` subject stats) stayed in the deck.
- **Records**: full SHAs everywhere. `picks[sha]` = the `picks --json` record plus `lane`, `from_lane`, `date`; `merges[sha]` = the `merge --json` record plus `lane`, `from_lane`, `date`, `subject`, `class` (worst file label, with `left-only`/`right-only` counting as `differs`) and `counts` (labels → counts, including equal files). Only parent merges are checked; peer merges have no class. Segment items carry their record's `class`.
- **Size**: the draft's "normalized per-file patch pairs" made disky's bundle 25MB. Instead, a file carries its `didi` (the outer unified diff of the two `index`-normalized patches) only when its label doesn't explain it: `differs`, `equal@ws`, `equal@tokens`. `equal@lines`/`equal@context`, one-sided, `absorbed` and `adopted` files carry just their label and counts. A didi is capped at 200 lines per file and 2000 per item (`didi_lines` holds the full count), and the output is compact JSON. disky (8 lanes, 572 picks, 566 merges): 4.3MB, 1.0MB gzipped, about 2.5 min from scratch (merges checked 8-way parallel).
- **Incremental**: `-p` reuses a pick record when its commit and resolved source are unchanged, and a merge record by SHA, if the previous report's `version` and `options` (`-n`/`-U`/`-w`/`-M`/`-C`) match. A rerun with nothing new takes about 12s on disky (segments are always recomputed). A record-format change must bump `REPORT_VERSION`.
- **Deck switch**: that's a `disky-deep-dive` change; see the spec written there.

### Phase 2: `www/` (lanes with classes, detail view, GH Pages)

- Vite + React 19 + TS + SASS, port 6092 (hash of the name). `@tanstack/react-query` fetches `?r=`; drag-and-drop or a file picker loads a local report; `?item=<sha>` (via `use-prms`) opens a pick's or merge's detail. LM/DM via CSS tokens (`prefers-color-scheme`, overridable by a header toggle persisted in LS).
- **Lanes** (`GitDag.tsx`): ported from the deck, minus deck-only marks/glyphs and the pie/badge toggle (picks are always wedges). Links are classed by their worst item. Tones: `quiet` (`equal`/`absorbed`/`adopted`, drawn as in the deck), `soft` (`equal@*`, `missing`: an amber halo), `loud` (`differs`/`files-differ`: a pink halo plus a `≠`/`files` badge). The draft had level badges on every non-equal link, but at disky's ~37px per day they buried the chart; the level is in the hint strip and the drawer instead. Drawer rows carry class chips (with tooltips) and open the item's detail.
- **Detail** (`Detail.tsx`, `didi.ts`): header (lanes, class, subject, SHAs linked to GitHub, sizes before/after and absorbed picks for merges), the reproducing `git-didi` command with a copy button, and files grouped by label: `differs`/`equal@tokens`/`equal@ws` open with their didi; one-sided, `equal@lines`/`equal@context`, `adopted` and `absorbed` folded; equal files as a count. A didi row has an outer gutter (side) and an inner gutter (sign); background by inner sign, a left rule and gutter colour by outer side; inner line numbers wherever an inner hunk header is inside the window, linked to the blob on GitHub (post-image lines at the side's tip; pre-image lines only where the base is a known SHA, i.e. merges). Token highlights: *inner* (a side's `-` line paired with its following `+` line: the edit) and *outer* (an outer `-` line paired with the outer `+` line of the same inner sign: what differs between sides). The `Treemap.tsx` case shows `, stagedRings = true` as the inner edit on both sides and the parent's `readLogged = allLogged, readUnlogged = [],` as the outer difference. Long lines wrap by default (toggle, persisted).
- **Audit table**: per lane, picks received and parent merges counted by class, plus the list of loud items (newest first), each opening its detail.
- **Tests**: vitest for `parseDidi` (gutters, line numbers, both highlight kinds) and `buildDag` (binning, link classes, prelude). Verified in Chrome against disky's report, in both themes.
- **Pages**: `.github/workflows/pages.yml` builds, tests and deploys `www/` on pushes to `main` touching it. The repo's Pages source must be set to "GitHub Actions" once (`gh api -X POST repos/runsascoded/git-didi/pages -f build_type=workflow`). Not yet in the bundle: a sample report, so the published page opens on its landing form.

### Phase 3: pair tracks and the own-delta trend

- **Report** (`pairs`, `REPORT_VERSION` 2): per (child, parent) lane pair, the child's first-parent commits as rows, oldest first: `pick` (`dir: down`, a trailer pick from the parent; `dir: up`, a child commit the parent picked by trailer; or, for unpaired child commits whose `git patch-id --stable` equals an unpaired parent spine commit's, `via: patch-id`, direction by which side committed first, classified like a pick, with non-equal `files` on the row since there's no pick record), `merge` (with its class), and `own` (unpaired: the intrinsic delta; picks from other lanes stay `own` with `from_lane`). Own rows get `surfaces` (names of the lane's configured surfaces their paths touch, plus `other` for paths in none) and `n_paths`. Rows are recomputed on every run, `-p` or not (patch-ids are cheap: about 5s for disky's 7 pairs).
- **Surfaces**: generalized from the draft's `{intrinsic: [globs], parity: [globs]}` to any `{name: [patterns]}`; a pattern without glob characters is a path prefix (`site/src` matches `site/src/App.tsx`), and a surface named `parity` flags the commit in the viewer. disky's deployment lanes use `config` / `migrations` / `deploy` / `docs` / `parity` (`cp.yml`'s shared-code dirs).
- **Viewer** (`PairTracks.tsx`, `?pair=<child>` or `?pair=<child>,<parent>`, an Audit / Pair tracks switch under the lanes): a summary line (merges, picks each way, patch-id pairs, intrinsic commits, parity hits); the trend (own delta's changed lines or files, before → after at each merge: drops are what the merge absorbed, rises are new own work; dots coloured by the merge's tone, opening its detail); and the tracks: parent-side SHA | arrow + class | child commit. Consecutive pairs with an empty residual at their level (`equal`, `equal@lines/context/ws`, `absorbed`, `adopted`) collapse into a band ("2 picks, 2 merges, empty residual (4 equal)"); others stay open with their didi files (picks) or a residual summary + "open" (merges); runs of own commits collapse into "N intrinsic commits · deploy 8, docs 5, …", with parity-touching ones listed even when collapsed (⚠).
- **Fix found on the way**: `--since=YYYY-MM-DD` is git approxidate for that date *at the current time of day*, so a run late in the day dropped that day's earlier commits (36 on `gcs`'s first day). `since_arg` pins bare dates to midnight, for `report` and `picks -s` alike; the earlier disky numbers in [audit] were computed with the bug.

[audit]: done/delta-invariance-audit.md
[gitdag]: https://github.com/Open-Athena/ddd/blob/main/react/GitDag.tsx
[GitDag]: https://github.com/Open-Athena/ddd/blob/main/react/GitDag.tsx
