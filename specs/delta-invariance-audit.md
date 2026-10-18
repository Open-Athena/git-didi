# Normalized diff-of-diffs for long-lived branch audits

**Status:** implemented 2026-10-17 (see [Implementation notes](#implementation-notes)); drafted 2026-10-17, from the disky branch-harmony analysis (`~/c/oa/disky-deep-dive/data/branch-similarity.json`, `scripts/branch_similarity.py`). Consumer: disky's `scripts/branch-audit` and the proposed [`branch-harmony-audit.md`][disky spec].

## Why

disky runs one base branch (`cloud`) and several long-lived deployment branches (`gcs`, `cw`, `hf`, `r2`, `local` → `m3`, `tauri-native-app`). Changes flow up by `cherry-pick -x` and down by `merge`. Two questions should be answerable mechanically:

1. **Pick fidelity**: is each pick the same patch as its source?
2. **Delta invariance**: is a child's own delta (`merge-base(child, parent)..child`) the same before and after it merges its parent, apart from what the merge legitimately absorbs?

Today `git-didi patch` answers both only as "byte-identical or not", and real cases are noisy:

- Of 405 picks (with a resolvable `-x` source) landed on disky's long-lived branches since 2026-09-28: 277 are identical patches (modulo `index` lines), 54 differ only in hunk line numbers (equal `git patch-id --stable`), 30 differ only in context lines (same `+`/`-` lines per file), 31 differ in content, 13 touch a different file set (adapted picks).
- Of 18 recent `cloud` → `gcs`/`cw` merges, 8 have an empty didi of the own delta. Of the other 10, the three largest residuals (100–990 changed lines) are almost entirely picks of the child's own commits that the merge brings back ("absorbed"), and the small remainders are either pure line-number shifts (e.g. `gcs` merge `a96bd9b3`, `cloud/src/dt_cloud/cli.py`: only `@@ -3159` → `@@ -3200` differs) or the same edit re-applied to a line the parent also changed (`site/src/Treemap.tsx`: gcs's `, stagedRings = true` appended to a signature that `cloud` had just extended). An empty didi is too strict to use as an alarm.

## Bugs / gaps found while running it

- `normalize_diff` (strips `index` SHAs) was applied to the `patch` path's equality check, but the diff-of-diffs *display* used the raw patches, so every differing file showed `-index …`/`+index …` noise. (The draft said it was never called; it was, just not for display.)
- `patch` and `stat` exit 0 whether or not patches differ; callers grep stderr for "No differences in patches". Exit 1 on difference, like `diff`.
- One `git diff` subprocess per file per side. Fine at tens of files, slow at disky's ~250-file deltas; a single `git diff` per side, split on `diff --git`, would do.

## Features

### 1. Normalization levels: `-n/--normalize LEVEL` (cumulative)

| level | ignores |
|---|---|
| `none` | nothing (today's behavior, minus the index bug) |
| `index` (default) | `index <sha>..<sha>` lines |
| `lines` | hunk-header line numbers: `@@ -a,b +c,d @@ ctx` → `@@ ctx`; equals `git patch-id --stable` semantics |
| `context` | context lines; compare each file's ordered `+`/`-` lines only (hunk boundaries dropped) |
| `ws` | whitespace within changed lines (like `-w`, but applied to the patch text) |

Report per file which level first makes it equal, so a run classifies rather than just fails. The output summary becomes e.g. `12 equal, 3 equal@lines, 1 equal@context, 2 differ`.

### 2. `--json`

One record per file: `{path, status, level, left_lines, right_lines}`, `status` ∈ `equal | differs | left-only | right-only`. Lets callers (disky's audit, CI) threshold without parsing colored text.

### 3. `didi picks [RANGE]`

For each commit in `RANGE` with a `(cherry picked from commit X)` trailer, compare `X^..X` with `C^..C` at the requested normalization; report class counts and list the non-equal ones. Fast path: equal `git patch-id --stable` ⇒ `equal@lines` without running the diff-of-diffs. A missing source (rewritten history) is its own class.

### 4. `didi delta CHILD PARENT --at A --at B` (merge invariance)

Compare a child's own delta at two points: `mb(CHILD@A, PARENT@A)..CHILD@A` vs `mb(CHILD@B, PARENT@B)..CHILD@B`. Shorthand `didi merge M` = `mb(M^1, M^2)..M^1` vs `M^2..M`.

**Absorption.** Commits in `M^1..M^2` that are picks of commits already in `M^1` (trailer, or patch-id match) are expected to shrink the delta. Subtract them: a file whose residual is fully explained by absorbed picks is `absorbed`, not `differs`. In disky's sample, 3 of 6 non-empty merge didis were fully or almost fully absorbed (`41c980dd`: 11 files, 10 explained).

Report: files `equal`/`absorbed`/`equal@<level>`/`differs`, and the delta's size before and after (`+/-` lines), so a caller can trend it.

### 5. Token-level ("first-order") mode, later AST

`--normalize tokens`: for each changed hunk, express the edit as a token-level edit script (word-diff of the `-` vs `+` lines) and compare scripts rather than lines. The `Treemap.tsx` case above becomes "insert `, stagedRings = true` before `}: {`" on both sides, i.e. equal, even though the full lines differ because the parent changed other tokens on them.

A tree-sitter mode (`--normalize ast`) would generalize this: edits as AST operations (add parameter, add import, change literal), equal when the operation set matches. Out of scope for a first cut; the token mode covers the common "both sides edited the same line" case.

## Non-goals

- Deciding what the intrinsic delta *should* be; that is the consumer's ledger.
- Rewriting history or resolving conflicts.


## Implementation notes

Code: `src/didi/normalize.py` (splitting, levels), `src/didi/compare.py` (batched git plumbing, classification, picks, delta/absorption), commands in `cli.py`. Tests: `tests/test_normalize.py`, `tests/test_audit.py` (fixture repo covering every pick/merge class).

- **Bugs**: display now diffs `index`-normalized patches (raw only with `-n none`). `patch`, `stat`, `picks`, `merge`, `delta` exit 1 on differences, 2 on errors; usage errors in `patch`/`stat`/`commits` moved from 1 to 2 (BIC, `diff` convention). `patch` runs one `git diff` per side and splits on `diff --git` (paths from `rename to`/`+++`/`---`, falling back to the `diff --git` line for binary/mode-only changes; C-quoted paths unquoted). Output format is pinned against user config (`--src-prefix=a/ --dst-prefix=b/ --no-ext-diff --no-color`, `core.quotePath=false`).
- **Levels**: `none index lines context ws tokens`. `-n` is the *threshold* (files equal at or below it count as equal, for display and exit status); every file is still classified by its first equal level, and the summary labels `none`/`index` as plain `equal`. `lines` is not exactly `git patch-id --stable` semantics: patch-id also ignores whitespace, so it sits between `lines` and `ws`.
- **`tokens`** (feature 5, first cut): each run of `-`/`+` lines between context lines becomes a `difflib` opcode list over `\w+|[^\w\s]` tokens, rendered as `insert: …` / `delete: …` / `replace: … → …`, with no position anchor. The `Treemap.tsx` case classifies `equal@tokens`. AST mode not implemented.
- **`--json`**: `patch` emits `{path, [left_path], status, level, left_lines, right_lines}`; `picks` adds per-pick `{commit, subject, source, class, level, ok, files}`; `merge`/`delta` emit `{label, before, after, absorbed_commits, ok, files}`, files carrying `residual_lines`/`absorbed_lines` when absorption was attempted.
- **`picks [REVS]`**: `git log` args (default `HEAD`), `-s/--since`. Trailers found via one `git log --grep`; the *last* trailer is the immediate source; sources resolved with one `cat-file --batch-check`; all patches fetched with one `git log --no-walk --stdin -p --diff-merges=first-parent`. The patch-id fast path was dropped: computing patch-ids needs the same diffs, and batching made the whole run cheap (403 disky picks in 0.85s). `missing` picks are listed but don't fail the run.
- **`delta`**: two forms, `delta CHILD PARENT --at A [--at B]` (`--at` is a reflog selector, `CHILD@{A}`; the second point defaults to current tips) and `delta CHILD_A PARENT_A CHILD_B PARENT_B`. `merge M...` takes merges or ranges (expanded to first-parent merges).
- **Absorption**: candidates are commits that entered the base and weren't in the old child (`base_B ^tip_A`, i.e. `M^1..M^2` for a merge). A candidate is absorbed if its trailer names one of the child's own commits, an own commit's trailer names it (the child had picked it from the parent), or patch-ids match. Per file, residual lines the delta *lost* must appear (same sign) among the absorbed picks' changes; lines it *gained* must appear with flipped sign (e.g. own commits `x→y→z`, only `x→y` absorbed: the delta becomes `-y +z`).
- **`adopted`** (new class, not in the draft): the parent often takes the child's content without a pick, e.g. disky `d8196490ec`, where one `cloud` commit took 24 of `gcs`'s spec files wholesale. Lost lines may also be explained by the base's net change `git diff base_A base_B`; such files are `adopted`. Gained lines are never explained that way, so a merge that drops a parent change still `differs`.
- **Delta report**: per-merge `files +added/-removed` before → after, absorbed-pick count, class summary, non-equal files (with `N/M residual lines explained` for partial absorption), and the `git-didi patch` command reproducing the comparison.

### Results on disky (2026-10-17)

- `picks -s 2026-09-28 cloud gcs cw hf r2 m3 local tauri-native-app`: 403 picks, 277 equal, 52 equal@lines, 30 equal@context, 1 equal@ws, 13 equal@tokens, 18 differs, 12 files-differ (draft's hand count: 405 / 277 / 54 / 30 / 31 content / 13 file-set).
- `merge a96bd9b3`: 235 equal, `cli.py` equal@lines, `Treemap.tsx` equal@tokens. `merge 41c980dd`: 4 absorbed picks, 10 of 11 residual files absorbed; the remaining `Dockerfile` really differs (the merge dropped `--extra plot` from `uv sync`).
- All 212 `cloud` → `gcs`/`cw*` first-parent merges since 2026-09-28 (~0.5s each): MERGE_RESULTS

[disky spec]: ../../disky/specs/branch-harmony-audit.md
