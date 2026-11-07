# CONTEXT — performance history and constraints for code-health work

Background for anyone changing the code for speed or structure, alongside `reports/tech_debt_audit.md`.
CLAUDE.md stays authoritative for the methodology. This file records what has already been tried, what it
showed, and the rules that came out of it.

## Why speed matters here: timeouts change results
Ablation runs (`cli.py ablate`) put a per-variant wall-clock cap on each run (900 s, `--jobs 8`, on the compute
machine). A run that hits it is recorded as `timeout`, so on heavy units **scoring speed decides an outcome**.
Stage A had 5 V1 timeouts. In U19, V1b vs V1 differed by exactly one result, and it was a timeout flip (seed
23). In Stage A2, 4 of 6 timeout units changed status between A and A2. So a speed-up is not just
convenience: it can move accuracy numbers. That is why identical-result speed-ups and result-changing ones
are handled differently (below).

## History of performance work (oldest first)

| When | What | Result | Where |
|---|---|---|---|
| Session 3 | Robust variant (≤1 unmatched event, probabilistic silence) | Many more survivors; scoring 276 hypotheses took 23 s on clean data | docs/history.md "Two design variants" |
| Session 4, P0.1 i | Best-first hypothesis enumeration with separate full/partial caps and a budget; a CPA radius cap; weak events don't gate pruning (V1) | Bounds hypotheses per cell; caps are config knobs | `cascade.py`, `reports/apriori_registry.md` |
| Session 4, P0.4 | `ablate` per-variant wall-clock cap, `--jobs`, free-space guard | Makes long runs bounded; makes timeouts an outcome | `cli.py cmd_ablate` |
| Session 5, step 1b | cProfile of four heavy V1 units (51–79 k hypotheses) | Scoring 96–99 % of the run; centre scoring 88–91 %, of which the per-hypothesis Δt refinement (`minimize_scalar`, ~18 `_hyp_cost` calls each) 72–75 %; `modeled_ratio` ~74 %, `TrackSet.sample` ~48 %. **Per-call numpy overhead on ~60-point arrays, not arithmetic.** In-cell refinement 7–8 %, cascade 1–4 % | `reports/stageA.md` §"step 1b" |
| Session 5 (7949796) | Direct-row `TrackSet.sample` for a scalar track; `np.maximum/minimum` and `sqrt(Σx²)` in `modeled_ratio` | Bit-identical on 10 rebuilt units × {V0, V1}; 10–14 % faster on heavy units (702 → 621 s); did not clear the timeouts | commit 7949796 |
| Session 6 (#1, #3) | CI: sharded gate, xdist, package cache, `heavy` marker, slow suite after merge | Gate about 30 s (was 3.5 min) | `.github/workflows/tests.yml`, CONTRIBUTING §7 |
| Session 8 (#15, f809b80) | Batched refinement: the 3×3×13 start grid in one call; each L-BFGS-B point costed with its 3 finite-difference points via scipy's `workers` hook; packed (T−1, 12) gather in `TrackSet._sample_one` | Bit-identical (frozen oracle `tests/_scoring_oracle.py`, 10 ablation rows, Stage A2 cross-check 100/100). `rank_cells` 40.5 → 22.2 s on a refinement-heavy scenario; **only 1.16–1.29× on hypothesis-heavy units (19–34 k hypotheses), where centre scoring dominates and was untouched** | PR #15 |
| Session 8 (#16) | Stage A2 full pass | 275 units in about 53 min vs Stage A's 69 min | `reports/stageA.md` §A2 |

Where the time goes now: on the units that time out, it is **centre scoring of every hypothesis** (a coarse
Δt grid plus a Brent `minimize_scalar` per hypothesis). Refinement was the target of #15 and is no longer the
bottleneck there.

## Options already identified but not done
- **Lockstep Δt refinement (identical-result).** Refine all of a cell's hypotheses together, so each event and
  track is interpolated once per step for all of them. Proposed in session 5. It needs the same bit-for-bit
  check as #15, and it is the main lever left for the timeouts. (Audit item 4.)
- **Two-stage refinement (result-changing).** Run full L-BFGS-B only for the top K cells by start-grid score.
  NEXT_SESSION lists it as a registered arm, with K fixed in advance, decided against the Stage A2 rows.
- **Top-K hypotheses per cell, or a lower hypothesis cap (result-changing).** These stay measured arms
  (stageA.md step 1b).
- **Ragged `TrackSet` (memory, US-wide).** A known pitfall in CLAUDE.md that nothing has addressed yet. The
  packed gather in #15 is per track, so a ragged layout can keep it. (Audit item 8.)
- numba and multiprocessing inside a run have not been tried. CLAUDE.md asks for the hot spot to be flagged
  (done above) before reaching for them.

## Rules that came out of this history
1. **Identical-result speed-ups** go straight in, but only with proof: every result field must reproduce
   against a frozen copy of the old code (`tests/_scoring_oracle.py` pattern) and on rebuilt ablation units,
   with only provenance and timing fields differing. Expect differences in the 7th digit across machines
   (geoid grids present or not). Compare on one machine only.
2. **Result-changing speed-ups** are arms. Register them in `reports/apriori_registry.md` before any
   measurement, keep them off by default, and decide them against the current reference rows (Stage A2,
   `reports/data/stageA2_rows.jsonl.gz`), never by tuning on synthetic data.
3. **Timing comparisons** are wall-clock on the compute machine, with nothing else running. The Stage A2
   caveat shows a parallel test run can flip a timeout.
4. **Smoke run first** (CONTRIBUTING §4.1) before any run over about 15 min.
5. **Profile before optimizing.** Every speed-up so far started from a cProfile of the slowest units.
6. **Keep V0 and V1 (and the `-preU19` / `-asdesigned` arms) runnable.** Speed and refactoring work must not
   remove a variant's code path.
7. **Config changes move `config_sha256`.** Adding keys to `config.yaml` is allowed, but it changes the hash
   that rows record. Do it in a PR of its own, and say so in the PR.

## Concurrent work
None as of 2026-11-06. The U19 task 4 work (arm h / h1g) merged in #17. Its screens ruled neither arm out, and
both stay off by default (`reports/u19.md`, task 4 steps 2 and 3). #17 added keys to `config.yaml`, so rows made
after it record a new `config_sha256` and `detection_sha256` with unchanged results (rule 7). Two-stage refinement
is on hold, pending the order of it and the identical-result centre-scoring speed-up (audit item 4).
