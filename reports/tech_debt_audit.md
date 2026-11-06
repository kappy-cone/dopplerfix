# Tech debt audit (2026-11-06, main at f645e0d)

Scope: `doppler/` (19 modules, about 7.4 k lines), `cli.py` (2,038 lines), `tests/` (27 files), CI,
dependencies, `config.yaml` and the top-level docs. `reports/experiments/` is covered only by an import check.
Each item is scored with **Priority = (Impact + Risk) × (6 − Effort)**, each axis 1–5.

## What is already in good shape (not debt)
- **Config hygiene for the parameter dataclasses.** `tests/test_config.py` makes every dataclass field have a
  `config.yaml` key and every key have a reader. The repo has no TODO, FIXME or HACK comments.
- **Purity rule.** Apart from `export.py` and `plot.py`, no module in `doppler/` does file I/O (the only
  `json.dumps` is the commitment hash in memory).
- **CI.** The merge gate is sharded, pinned and cached, and heavy tests run after merge (CONTRIBUTING §7).
- **Experiment scripts.** All 36 scripts in `reports/experiments/` still import cleanly against the current
  `doppler`/`cli` names.
- **The two design variants** (original vs robust, plus the `variants-asdesigned` tag) are deliberate (HANDOFF,
  session 3). They are not debt to remove.

## Prioritized list

| # | Item | Type | Impact | Risk | Effort | **Priority** |
|---|------|------|:-:|:-:|:-:|:-:|
| 1 | Third-party video transcript committed to a public repo (`doppler.txt`) | Docs / legal | 1 | 4 | 1 | **25** |
| 2 | 23 config defaults duplicated inline in `cli.py` (`.get("key", value)`) | Code | 2 | 3 | 1 | **25** |
| 3 | `cli.py` is a 2 k-line hub: `run_locate` is about 350 lines, plus a 500-line ablation harness | Architecture | 4 | 3 | 3 | **21** |
| 4 | Scoring is a per-hypothesis Python loop (`score.rank_cells`), about 95 % of V1's time | Architecture / perf | 4 | 3 | 3 | **21** |
| 5 | Stale session docs: HANDOFF "START HERE" says session 6; CLAUDE.md's layout has drifted | Docs | 2 | 2 | 1 | **20** |
| 6 | Only direct dependencies are pinned; there is no lock for transitive packages | Dependency | 2 | 3 | 2 | **20** |
| 7 | Hypothesis enumeration in the offset filter loops over cells in Python (`cascade.py:499`) | Architecture / perf | 2 | 4 | 3 | **18** |
| 8 | Dense `TrackSet` (K, T, 3) float64: US-wide blocker (known, CLAUDE.md pitfall) | Architecture | 3 | 5 | 4 | **16** |
| 9 | No lint or type check in CI, although the conventions require type hints | Infrastructure | 2 | 2 | 2 | **16** |
| 10 | `reports/experiments/` is never imported by CI (`paths-ignore: reports/**`) | Test | 1 | 2 | 1 | **15** |
| 11 | `score.py` imports `cascade._refine_cpa`, a private function | Code | 1 | 1 | 1 | **10** |

| 12 | One test takes 167 s and sets the fast suite's wall time (`test_nulltest.py`) | Test | 3 | 2 | 2 | **20** |
| 13 | pandas 4 deprecation warnings, via fastparquet and `Series.values` on tz-aware data | Dependency | 1 | 3 | 2 | **16** |

**Test-suite health (run during this audit, `-m "not slow" -n 4`):** 216 passed, 1 skipped, 174 s wall
time. The skipped test is the geoid-grid test, which needs `data/proj`, and this checkout doesn't have it.
There are no failures and no flaky retries.

Item 8 scores below its importance because the framework rewards low effort. It is on the critical path to
milestone 7 (the US-wide benchmark), so it is scheduled by that deadline rather than by its score (see Phase 3).

## Items

### 1. `doppler.txt`: a YouTube transcript in a public repo (25)
The file is a transcript of someone else's video (839 lines), committed in e181f21 and cited by seven reports
plus HANDOFF. Publishing a full transcript of third-party content is a copyright exposure for a public repo,
and the analysis needs only a summary and the video's link.
**Fix (about 30 min):** move it to gitignored `data/`, replace it with a short summary plus the URL, and update
the references. The file stays in git history; rewriting history is a separate decision for you.

### 2. Inline config defaults in `cli.py` (25)
Examples include `cli.py:262`/`:730` `clock_tol_s` 10.0, `:704` `height_agl_m` 1.5, `:274` `spacing_m` 200,
and `:1176`–`:1192` the output caps. These keys are read outside the dataclasses, so `test_config.py` does not
cover them. If a key is renamed or removed in `config.yaml`, the code silently falls back to a hard-coded value
that `config_provenance` does not see. That breaks "all thresholds in config.yaml" and the reproducibility of
the provenance hash.
**Fix (about 1 h):** read these keys strictly (KeyError when missing), or move them into small `MicParams` /
`GridParams` / `OutputParams` dataclasses that `test_config.py` already knows how to check.

### 3. `cli.py` as a hub (21)
`run_locate` (cli.py:688–1037) runs audio, tracks, truth, cascade, scoring, ambiguity and evaluation in one
function. `write_outputs` is about 200 lines, and the ablation harness (`ablation_plan` through
`ablate_summary`, cli.py:1418–1911) lives in the CLI too. This works against the HITL design constraint
(stages separable, with explicit inputs and outputs). Milestone 9 has to insert analyst decisions between
stages, and today each stage is only a span of lines inside `run_locate`. It also makes the ablation logic
hard to unit-test without going through the CLI.
**Fix (2–3 days, incremental):** split `run_locate` into stage functions with typed inputs and outputs
(`events_stage`, `tracks_stage`, `cascade_stage`, `score_stage`, `evaluate_stage`). Each step must leave the
outputs bit-identical, checked against a frozen Stage A2 row set. Then move the pure parts of the ablation
harness (planning, row building, paired tests) to `doppler/ablation.py`, keeping process and disk handling in
`cli.py`.

### 4. Scoring hot spot (21)
`rank_cells` scores every hypothesis of every cell with `score_hypothesis` in a Python loop, then refines the
top cells with per-cell L-BFGS-B. #15 batched the refinement's cost evaluations (about 1.8× faster), but the
centre-scoring loop is still per hypothesis, and scoring dominates the wall clock of ablation runs (Stage A
took about 1 h 40 min at `--jobs 8`). CLAUDE.md says to flag this before reaching for numba or
multiprocessing.
**Fix:** profile first (½ day, `cProfile` on one Stage A unit). Then batch the centre scoring across
hypotheses that share a cell (same ridges, stacked track rows), as #15 did for refinement, and check it
against `tests/_scoring_oracle.py`.

### 5. Stale session docs (20)
- `HANDOFF.md` opens with "START HERE (session 6 … branch `s6-u19-detection`)", but main is past U19 and
  Stage A2 (#14–#16). Its 337 lines keep superseded sections for sessions 3–5 (it says so itself).
- In CLAUDE.md's repo layout, `adsblol.py`, `nulltest.py` and `commitment.py` are missing, and so are the
  `regions`, `adsblol` and `verify-result` subcommands. `countermeasures.py` and `cli.py countermeasures` are
  listed but don't exist yet (milestone 8); mark them as planned.
**Fix (about 1 h):** rewrite the top of HANDOFF for the current state, move sessions 3–5 to a
`docs/history.md` or link them from git tags, and correct the layout block.

### 6. No lock for transitive dependencies (20)
`requirements.txt` pins 16 direct packages. Everything under them (GDAL inside the rasterio wheel, PROJ inside
pyproj, numexpr, tzdata, …) floats. The CI cache is keyed on `requirements.txt`, so a cache miss months later
can install different transitive versions than the cache it replaces. That matters for a project whose
validation compares bit-identical rows across runs.
**Fix (about 1 h):** generate a full lock (`pip freeze` from the CI venv into `requirements.lock`, or
`pip-compile`), install from it in CI and on the compute machine, and key the cache on it.

### 7. Offset filter loops over cells in Python (18)
`cascade.py:499–560` enumerates each surviving cell's hypotheses best-first with a heap, cell by cell. This is
fine at regional scale. CLAUDE.md requires the US-wide cascade to be vectorized and spatially pruned before
step 7, and this loop scales with the number of offset survivors.
**Fix:** measure the survivor count on the first US-wide dry run before changing anything. If it is large,
vectorize the N = 2 case (most clips) and keep the heap only for N ≥ 3 or for capped cells.

### 8. Dense `TrackSet` (16, but time-critical)
This is already recorded in CLAUDE.md: (K, T, 3) float64 `xyz` and `vel`, plus `h_m` and `ground_speed_mps`.
5,000 tracks × 10 min at 0.25 s is about 0.6 GB. Each row is padded with NaN outside the segment's coverage,
so short segments in a long window waste most of their row.
**Fix (3–5 days):** store ragged per-track arrays (an offsets array plus concatenated samples). Keep the
sampling API (`ts.xyz[track]`, `t_s`) behind accessor functions first, so the cascade and scoring change in
one place. Do this before milestone 7, and write it up as an ADR first (chunk-by-time vs ragged), because it
touches cascade, score, plot and export.

### 9. No lint or type check in CI (16)
There is no ruff, mypy or pre-commit config, although the conventions ask for type hints on public functions.
**Fix (½ day):** add `ruff check` (default rules) to the gate. It takes about 1 s, so it fits the 30 s budget.
Add mypy later, on `doppler/physics.py` and `doppler/geo.py` first.

### 10. Experiment scripts can rot unseen (15)
They import cleanly today, but CI skips `reports/**` and no test imports them, so a rename in `doppler/` can
break a script that a report depends on for reproduction.
**Fix (about 30 min):** add a gate test that AST-parses `reports/experiments/**/*.py` and checks that every
name imported from `doppler`/`cli` exists, without running the scripts. A test of this kind found no stale
names today.

### 12. One test dominates the fast suite (20)
`test_nulltest.py::test_unshifted_search_reproduces_the_ranking_and_shifts_fit_worse` takes 167 s. The next
slowest are `test_score.py::test_rank_cells_is_bit_identical_to_the_frozen_oracle` (81 s) and the four
`test_true_cell_ranks_first_after_locked_scoring` cases (33–60 s each). With 4 workers, the null test alone
is most of the 174 s run, so local feedback is about 3 min whatever else changes. If a CI shard lands this
test together with another slow one, it threatens the gate's 5-minute timeout.
**Fix (½ day):** shrink the null test's scenario (fewer shifts, a smaller region) for the fast suite, and
mark the full version `heavy`. Item 4's faster scoring also helps here.

### 13. pandas 4 deprecations (16)
pandas 3.1 already warns that `future.infer_string` (read by fastparquet) and `Series.values` dropping the
timezone will change in pandas 4. The second one is in our own code path (`test_export.py::test_parquet_roundtrip_zstd`).
**Fix (about 1 h):** use `.dt.tz_convert(None).to_numpy()` where we call `.values` on tz-aware series, and
turn warnings into errors in pytest (`filterwarnings = error::DeprecationWarning` plus the pandas warning
class) so the next ones surface at once. fastparquet's warning goes away with the planned pyarrow switch
(CLAUDE.md Conventions).

### 11. Private cross-module import (10)
`score.cpa_geometry` imports `cascade._refine_cpa`. **Fix (10 min):** make it public, or move it to `geo.py`.

## Phased remediation plan (alongside feature work)

**Phase 1: quick wins (about 1–2 days, before the next experiment run).** Items 1, 2, 5, 10 and 11, then 6,
9, 12 and 13. None of them changes a result. Item 2 changes how config is read, so confirm that the `config_provenance`
hash of a Stage A2 unit is unchanged.

**Phase 2: with the next Phase 1 plan items (P1.x).** Item 4: profile, then batch the centre scoring. It makes
every later ablation cheaper, so it pays for itself first. Item 3 goes alongside, one stage function per PR,
each checked bit-identical against Stage A2 rows.

**Phase 3: before milestone 7 (US-wide benchmark).** Item 8 (ADR, then ragged TrackSet), then item 7 if the
first US-wide dry run shows that the offset-filter loop matters. The stage split from item 3 is also what
milestone 9's decisions-as-data will hook into.
