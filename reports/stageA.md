# Stage A baseline (P0.6), session 5

**Run.** `cli.py ablate --plan stageA` on scenario C (60 s, events at 15/45 s), on the user's PC (Ryzen 7
9800X3D, 8 cores, 59 GB), `--jobs 8`, 900 s cap per run. Raw rows are in `outputs/ablation/stageA/rows.jsonl`
(gitignored, 650 rows; per-config table in `summary.md`, the run log in `run.log`).
- V0 and V1: commit 7ea5883, clean tree, all 275 units (all-on × 50, 9 LOO configs × 20, clean × 5, the
  skeptics' 4 experiments × 10).
- V0-asdesigned and V1-asdesigned: tag `variants-asdesigned` (worktree `../qgis-asdesigned`), all-on seeds
  0–49 (`--limit 50`; the plan asked for 0–19).
- Relabel fix (commit 385a725): the as-designed wrapper had recorded "no events detected" as `error`, which
  dropped 5 seeds per arm from every outcome column. After the fix those 5 units were rerun; they are now
  `abstained` / `survival:none`, the same row the in-process arms write. The as-designed rows record only the
  tag, not the driver commit, and `manifest.json` describes only that last 5-unit invocation.

Wall time: about 1 h 40 min in total (V0/V1 pass about 69 min, as-designed pass about 31 min). This
machine is about 2× the Mac's per-core speed; the first two pilot units reproduced the Mac's results exactly.

## Outcomes, all-on C (n = 50 paired seeds)

| arm | correct | abstained | wrong | confident-wrong | timeout | unique: correct / n |
|---|---|---|---|---|---|---|
| V0 (strict) | 2 | 37 | 2 | 9 | 0 | 2 / 13 |
| V1 (robust) | 7 | 32 | 2 | 7 | 2 | 7 / 16 |
| V0 → V1 fallback (zero-survivor trigger) | 5 | 33 | 2 | 10 | 0 | 5 / 17 |
| V0 → V1 fallback (absolute-fit trigger) | 7 | 29 | 2 | 11 | 1 | 7 / 20 |
| V0-asdesigned | 2 | 27 | 5 | 16 | 0 | 2 / 23 |
| V1-asdesigned | 12 | 18 | 3 | 12 | 5 | 12 / 27 |

Two-sided 95 % Clopper–Pearson intervals at n = 50: 2 → [0, 14] %, 7 → [6, 27] %, 9 → [9, 31] %,
12 → [13, 38] %, 16 → [20, 47] %.

Clean (n = 5): V0 and V1 both 4 correct (median error 7 m), 1 abstained (seed 2, 6.7 m off, labelled
`inconsistent`).

## Re-ordering rule (plan §3.0): written order

Failure mix on all-on C, over rows with a failure class:

| arm | classified failures | survival at CPA (grid + none) | survival:offset | discrimination | bias |
|---|---|---|---|---|---|
| V0 | 47 | 15 (32 %) | 15 (32 %) | 8 (17 %) | 8 (17 %) |
| V1 | 35 | 13 (37 %) | 0 | 13 (37 %) | 7 (20 %) |

No class reaches 40 %, so the written order stands: Phase 1 next. The pilot's indication (P3.5 before Phase 2,
from 5 seeds) does not hold at n = 50. V1 is close to the line on both survival and discrimination. The rule
does not apply to the as-designed arms, which report no failure taxonomy.

## The `inconsistent` status rejects correct answers (missed target, reported)

`score.absolute_fit_status` (`score.inconsistent_rms_sigma` = 3, PROVISIONAL) flags a rank-1 answer when any
matched event's rms exceeds 3 × σ_ref. It is a status only; ranking is unchanged.
- On all-on C, V1 had the right aircraft pair within 200 m on 7 seeds (5, 6, 16, 29, 35, 38, 42; 23–132 m
  off) that it labelled `inconsistent`, so they count as abstentions. V0: 1 (seed 38). Clean seed 2 too.
- This is most of the gap between V1 (7 correct) and V1-asdesigned (12 correct). Both code versions give the
  same rank-1 answer on 34 of the 39 seeds where both have one; on the 12 seeds where V1-asdesigned is
  correct, V1's estimate is identical on 11 (132 vs 152 m on seed 35). Only the label differs.
- It does not separate right from wrong. Fraction of rank-1 answers with max rms/σ_ref > 3:

  | | right (≤ 200 m, right pair) | wrong |
  |---|---|---|
  | V1, all-on | 7 / 15 (47 %) | 11 / 28 (39 %) |
  | V1, every config | 34 / 87 (39 %) | 67 / 148 (45 %) |
  | V0, every config | 10 / 22 (45 %) | 73 / 131 (56 %) |

  This is the overlap plan §3 P0.4 already expected from D-4 (Hz-level misfit of right and wrong answers
  overlaps under realism).
- It still removes some confident-wrong answers: 16 → 9 (V0) and 12 → 7 (V1) against the as-designed arms.
- Under the registry rule the threshold is **not** moved. P1.1 (calibrated likelihood) and P1.4 (ambiguity
  v2) are the planned replacements; Phase 1 should be measured against this baseline.

## Confident-wrong

V1 still gives 7 / 50 confident-wrong answers, and only 7 of its 16 `unique` answers are correct. Six of the
seven have the wrong aircraft pair at rank 1 (5 discrimination, 1 survival:grid); one (seed 19) has the right
pair 1.2 km off. V0: 9, eight with the wrong pair. Until P1.4 lands, `unique` is not trustworthy (plan §7).

## Leave-one-out and experiments (n = 20 or 10 per config; descriptive only)

Per-config tables are in `outputs/ablation/stageA/summary.md`. At n = 20 no paired binary comparison can
reach significance (plan §3.0, McNemar); paired continuous tests are not run yet. Points worth carrying
forward:
- `loo:path` (labelled provisional; reflection coefficient is real and positive): 8 / 20 seeds detect no
  events at all, in both variants.
- `sub:rpm`, `sub:cwind` and `sub:mic_height`: V1 correct 5–6 / 20 (all-on: 7 / 50) and V1 confident-wrong
  1–2 / 20.
- `loo:source` and `loo:adsb` leave V0 at 0 correct; its failures are spread over CPA (grid) and offset.

## Compute

| arm | mean s | median s | p90 s | max s |
|---|---|---|---|---|
| V0 | 14 | 2 | 20 | 672 |
| V1 | 92 | 32 | 231 | 900 (cap) |
| V0-asdesigned | 36 | 6 | 37 | 672 |
| V1-asdesigned | 175 | 39 | 793 | 900 (cap) |

Synthesis plus detection: about 15 s per scenario. Timeouts: 5 V1 rows (all-on 11 and 23, loo:scene 6,
sub:cwind 11, sub:mic_height 18) and 5 V1-asdesigned rows. V1 scored up to 69,348 hypotheses on one clip;
scoring is about 95 % of V1's time (profile `score.rank_cells` before numba or multiprocessing).

## Known gaps

- The as-designed arms report only the old run.json fields: no failure class, assignment rank or per-event z.
- The fit-error census [S-F2] is in the rows (`per_event`) but not summarised here.
- `manifest.json` is overwritten by each `ablate` invocation (it now describes the 5-unit relabel rerun);
  the rows carry their own commit.
