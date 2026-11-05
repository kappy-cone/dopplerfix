# Stage A baseline (P0.6), session 5

**Run.** `cli.py ablate --plan stageA` on scenario C (60 s, events at 15/45 s), on the user's compute machine, `--jobs 8`, 900 s cap per run. Raw rows are in `outputs/ablation/stageA/rows.jsonl`
(gitignored, 650 rows; per-config table in `summary.md`, the run log in `run.log`). A compressed copy for review
is committed as `reports/data/stageA_rows.jsonl.gz`, with `reports/data/stageA_detection.json` (per-unit truth and
detected event counts from the kept files, so the §8.1 detection measures can be recomputed without them).
- V0 and V1: commit 7ea5883, clean tree, all 275 units (all-on × 50, 9 LOO configs × 20, clean × 5, the
  skeptics' 4 experiments × 10).
- V0-asdesigned and V1-asdesigned: tag `variants-asdesigned` (worktree `../qgis-asdesigned`), all-on seeds
  0–49 (`--limit 50`; the plan asked for 0–19).
- Relabel fix (commit 385a725): the as-designed wrapper had recorded "no events detected" as `error`, which
  dropped 5 seeds per arm from every outcome column. After the fix those 5 units were rerun; they are now
  `abstained` / `survival:none`, the same row the in-process arms write. The as-designed rows record only the
  tag, not the driver commit, and `manifest.json` describes only that last 5-unit invocation.

Wall time: about 1 h 40 min in total (V0/V1 pass about 69 min, as-designed pass about 31 min). The
compute machine is about 2× the laptop's per-core speed; the first two pilot units reproduced the laptop's
results exactly.

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

## Addendum (session 5): plan §8.1 measures, re-summarised from the same rows

No re-run: `cli.py ablate --summarize` over the 650 Stage A rows, with the §8.1 measures added to
`doppler/evaluate.py` and the summary (`summary.md`, second and third tables). The pre-registered taxonomy and
re-ordering rule are unchanged.

**Hand-check first.** Eight rows were recomputed independently from their kept `truth.json` and
`events.parquet`: all-on 3, 1, 21, 10, 12 (V1), 13 (V0), clean 2, loo:path 6. They cover correct, bias,
discrimination, confident-wrong, no events and one event missed. Event counts, the truth matching, extra
events and per-event dv / dd / dt_rx all agree exactly. Two units (all-on seeds 1 and 3, V1) were also rebuilt
from their seeds and re-run in process; every Stage A field of the row came back identical.

**Detection counts for the other arms.** Timeout and no-event rows, and the as-designed arms, don't record
detection. Their counts come from the kept files: 169 rows, and 0 disagreements where a row did record one.
That is valid for the as-designed arms because detection is identical at the tag: `detect.py`, `ridge.py`,
`fit.py`, `classify.py`, `audio.py`, `cli.run_events` and the detect / ridge / fit / classify config sections
are unchanged between ea8fe0d and 7ea5883, and both arms analyse the same audio.

### Detection: a quarter of all-on truth events are never detected

| config (V1 rows) | truth events detected | runs with an event missed |
|---|---|---|
| clean | 10 / 10 | 0 / 5 |
| all-on | **75 / 100** | **20 / 50** |
| loo:levels | 38 / 40 | 2 / 20 |
| loo:scene | 37 / 40 | 3 / 20 |
| loo:source | 32 / 40 | 5 / 20 |
| loo:geometry, sub:rpm | 29 / 40 | 9 / 20 |
| loo:adsb, sub:cwind, sub:mic_height | 28 / 40 | 9–10 / 20 |
| sub:reflection | 12 / 20 | 5 / 10 |
| loo:path | 18 / 40 | 14 / 20 |
| set:corridor_frac=1, sub:corridor, sub:twins | 19, 18, 17 / 20 | 1, 1, 2 / 10 |

- §8.0 quotes all-on 19 / 20 from the 10-seed pathcheck. Stage A seeds 0–9 reproduce that exactly (also
  loo:path 10 / 20, sub:cwind 17 / 20; sub:reflection gives 12 / 20 against 14 / 20, which may be the pathcheck
  building scenarios without decoys). Seeds 10–49 give 56 / 80. Seeds 0–9 were the luckiest decade, so
  detection caps accuracy harder than §8.0 says: **20 of 50 all-on runs start with a truth event missing.**
- All arms share these counts (one detection per scenario).

**The `detection` class beside the taxonomy** (all-on, the 20 runs with a miss, by their pre-registered class):
- V1: survival:none 5, discrimination 8, bias 3, survival:grid 2, plus 1 correct and 1 abstained without a
  failure class. So 8 of V1's 13 "discrimination" failures and both survival:none ones are runs where the
  detector lost a truth event; the cascade or scoring never had a chance.
- V0: survival:none 5, survival:offset 6, survival:grid 4, discrimination 3, bias 2.

On the 30 fully detected all-on runs the rule still gives the written order (V1: survival:grid 6,
discrimination 5, bias 4, timeout 2; V0: offset 9, bias 6, grid 6, discrimination 5). That is reported for
information only; the pre-registered rule is applied to all runs, as before.

### Accuracy ignoring status, answer rate, and conditional accuracy (all-on C)

| arm | correct | rank-1 accuracy (status ignored) | answer rate (unique) | precision of unique | cond. n | cond. correct | cond. rank-1 acc | cond. precision |
|---|---|---|---|---|---|---|---|---|
| V0 | 2 | 3 | 13 | 2 / 13 | 30 | 2 | 3 | 2 / 8 |
| V1 | 7 | **15** | 16 | 7 / 16 (44 %) | 30 | 6 | 13 | 6 / 12 |
| V0 → V1 (zero survivors) | 5 | 7 | 17 | 5 / 17 | 30 | 5 | 7 | 5 / 12 |
| V0 → V1 (absolute fit) | 7 | 14 | 20 | 7 / 20 | 30 | 6 | 13 | 6 / 13 |
| V0-asdesigned | 2 | 3 | 23 | 2 / 23 | 30 | 2 | 3 | 2 / 15 |
| V1-asdesigned | 12 | **15** | 27 | 12 / 27 (44 %) | 30 | 11 | 13 | 11 / 21 |

(Rank-1 accuracy: right pair, where known, and within 200 m, whatever the label; the as-designed rows don't
record the assignment, so for them it is distance only, as in their outcome. "cond." = the 30 runs where
every truth event was detected.)
- **The localization baseline for U18 is 15 / 50 rank-1 accuracy (V1), with precision of unique at 44 %.**
  §8.1's estimate was ≈ 14: 7 correct, 7 labelled `inconsistent`, and seed 34 (185 m, `ambiguous`).
- V1 and V1-asdesigned have the **same** rank-1 accuracy, with no discordant seed. The Phase 0 code changes
  the labels, not the answers.
- Conditional on detection, V1 places the right pair within 200 m on 13 / 30 runs (43 %).

### Paired tests (plan §3.0)

Wilcoxon signed-rank on a − b over pairs with both values; exact sign test on rank-1 accuracy. The full list,
every config against all-on, is in `summary.md`.

| comparison | metric | pairs | non-zero | median a − b | p |
|---|---|---|---|---|---|
| V1 vs V0 (all-on) | rank-1 error | 31 | 24 | −355 m | 0.002 |
| V1 vs V0 (all-on) | true-assignment rank | 16 | 6 | 0 | 0.21 |
| V1 vs V0 (all-on) | rank-1 accuracy (V1 only / V0 only) | 50 | 13 / 1 | | 0.002 |
| V1 vs V1-asdesigned | rank-1 error | 39 | 7 | 0 | 0.13 |
| V1 vs V1-asdesigned | rank-1 accuracy | 50 | 0 / 0 | | — |
| V0 vs V0-asdesigned | rank-1 error | 33 | 7 | 0 | 0.04 |

- V1 beats V0 on both rank-1 error and rank-1 accuracy, the only clear arm difference.
- The Phase 0 fixes move few answers. V1 vs as-designed differs on 7 / 39 seeds, six of them by under 75 m (seed
  49: 1.6 vs 7.0 km). V0 vs as-designed differs on 7 / 33, mostly worse (seed 7: 3.8 km vs 444 m; seed 11:
  2.9 vs 2.5 km).
- No LOO config differs from all-on at p < 0.05 for V1 on any metric (n = 20, 10 for the experiments). The
  largest net rank-1 accuracy shifts are clean (4 / 0 discordant, more correct) and sub:reflection (0 / 3,
  fewer correct). For V0, loo:geometry gives 6 / 0 (p = 0.03).
- **Δcost is not computable from Stage A rows.** A rebuild of all-on seed 1 showed why: the true cell's best
  hypothesis (rank 69) leaves an event unmatched and scores 0.98, against rank 1's 8.34 with both events
  matched. Under lexicographic ranking these scores are not comparable, and the rows record neither the
  true assignment's score nor its unmatched count. Rows written from now on carry `true_assignment_score`,
  `true_assignment_n_unmatched`, `true_cell_n_unmatched` and `n_truth_events`. `evaluate.delta_cost` compares
  only hypotheses with the same unmatched count.

### What changes for the next steps
- U18's baseline is now explicit: V1 rank-1 accuracy 15 / 50 and precision of unique 44 % (7 / 16; V1-asdesigned
  12 / 27). P1.1 must raise precision without lowering rank-1 accuracy.
- Detection (§8.2, U19) matters more than §8.0 estimated: 20 / 50 all-on runs lose an event before the
  cascade, and those runs account for 8 of V1's 13 discrimination failures.

## Session 5, step 1b: where V1's time goes (profile of `score.rank_cells`)

cProfile of four V1 units rebuilt from their seeds: the two all-on timeouts (seeds 11 and 23), the
69,348-hypothesis clip (all-on 18) and the slowest finished unit (loo:source 6). Read-only; all four ran to
completion under the profiler (1,181, 1,231, 882 and 926 s).

| share of run time | all-on 11 | all-on 23 | all-on 18 | loo:source 6 |
|---|---|---|---|---|
| hypotheses scored | 78,814 | 65,168 | 69,348 | 51,936 |
| `rank_cells` (all scoring) | 99 % | 97 % | 99 % | 96 % |
| centre scoring (`score_hypothesis`) | 91 % | 90 % | 90 % | 88 % |
| of which the Δt refinement (`minimize_scalar`) | 75 % | 74 % | 74 % | 72 % |
| in-cell refinement (500 cells) | 7 % | 7 % | 8 % | 8 % |
| `modeled_ratio` | 75 % | 74 % | 74 % | 73 % |
| `TrackSet.sample` | 49 % | 48 % | 48 % | 48 % |
| cascade (CPA + offset + silence) | 1 % | 3 % | 1 % | 4 % |

- The time is per-call overhead, not arithmetic: each hypothesis' bounded Δt refinement calls `_hyp_cost` about
  18 times, one Δt at a time, on ~60-point arrays, and every call runs 6 track interpolations per event.
- **Identical-result speed-up (adopted, its own commit):** a direct-row path in `TrackSet.sample` for a single
  track, and `np.maximum`/`np.minimum` and `sqrt(Σx²)` in `modeled_ratio` in place of `np.clip` and
  `np.linalg.norm` (bit-identical here). Every Stage A field reproduces on 10 rebuilt units × {V0, V1} (all-on
  0–5 and 18, clean 0, loo:path 1, loo:adsb 4). V1 is 10–14 % faster on heavy units (all-on 18: 702 → 621 s).
  That is not enough to clear the two all-on timeouts.
- **Not done, for the user to decide:**
  - Result-changing options stay measured arms: top-K hypotheses per cell, or a lower hypothesis cap.
  - A larger identical-result option exists: refine the Δt of a cell's hypotheses in lockstep, so each event and
    track is evaluated once per step for all of them. It would need the same bit-for-bit check, and it is more
    work.

## Session 5, step 2: P1.1 + P1.4 arms (V1c, V0c), status before the U18 result

Implemented as comparison arms only; V0 and V1 rows are unchanged (the same 10 rebuilt units reproduce every
result field; only `config_sha256` differs, because `config.yaml` gained the new keys). The knobs and the
implementation choices are in `reports/apriori_registry.md` (Phase 1), registered before any measurement.

**Pre-registered P1.1 tests.** White noise (Wilks, 200 Monte Carlo trials, ADS-B budget off because the
simulation has no ADS-B error): 2Δnll(truth − MLE) over (E, N, Δt) passes the χ²₃ KS test (a 100-trial run gave
mean 3.20, KS p 0.86). **AR(1) ρ₁ = 0.6: missed.** Mean 2Δnll is 3.71 against 3 ± 20 % (n = 100), so the
effective-N correction under-corrects by about a quarter (without it the mean would be about 12). The test is
marked xfail with that value; nothing was tuned.

**Findings from building it** (no knob moved):
- The model-free ρ₁ / σ_white estimator is contaminated by the curve's own curvature when ridge noise is small
  (0.05 Hz): on white noise κ reached 3.6 for the event with the sharper CPA. At a realistic 0.3 Hz (Stage A
  σ_ref values are 0.2–0.4 Hz) it is close (κ ≈ 1.2). A 3-MAD trimmed version did not help.
- With the a-priori ADS-B budget, the latency term (ḟ × 0.5 s) dominates σ_model near CPA, where the curve is
  steepest. Treating a per-track systematic as white per-point variance down-weights the most informative part
  of each pass. This is the plan's P1.1 form ("later from P3.1's covariance"), measured as written.
- A non-robust self-referential σ_e (plain RMS) let one harmonic-hop point widen every point's density; σ_e is
  the robust (MAD) scale instead, so the mixture bound holds.
- V1c costs about 1.15–1.3× V1 per run (all-on 18: 802 vs 615 s), so it may time out slightly more often.

## Session 5, step 2 result: U18 acceptance — **V1c fails** (rank-1 accuracy drops)

**Run.** All-on C, seeds 0–99. V0c/V1c on seeds 0–49, and V0/V1/V0c/V1c on seeds 50–99 (`--plan stageA_ext`),
paired with Stage A's V0/V1 rows for seeds 0–49. `--jobs 8`, 900 s cap, 0 errors. The rows record commit
7057c08 (local tag `s5-u18-screen`). Its code (`doppler/`, `cli.py`, `config.yaml`, `tests/`) is identical to
`main` at 17da806, and the user accepted pushing it directly, so this counts as the decision run. V0/V1 on seeds
50–99 ran with the speed-up, whose results are bit-identical to the Stage A code. The new rows are committed as
`reports/data/u18_rows.jsonl.gz`, with `u18_detection.json` (all-on recall on seeds 50–99: 81 / 100).

| arm | n | correct | rank-1 acc (status ignored) | answer rate (unique) | precision of unique [95 % CI] | confident-wrong | timeout | cond. n | cond. rank-1 acc | cond. precision |
|---|---|---|---|---|---|---|---|---|---|---|
| V0 | 100 | 6 | 8 | 26 | 6 / 26 = 23 % [9, 44] | 18 | 0 | 65 | 8 | 6 / 18 |
| **V1** | 100 | 21 | **34** | 38 | 21 / 38 = 55 % [38, 71] | 11 | 3 | 65 | 31 | 20 / 32 |
| V0c | 100 | 4 | 7 | 10 | 4 / 10 = 40 % [12, 74] | 6 | 0 | 65 | 7 | 4 / 9 |
| **V1c** | 100 | 11 | **25** | 18 | **11 / 18 = 61 %** [36, 83] | 4 | 3 | 65 | 23 | 10 / 15 |
| V0-asdesigned | 50 | 2 | 3 | 23 | 2 / 23 = 9 % [1, 28] | 16 | 0 | 30 | 3 | 2 / 15 |
| V1-asdesigned | 50 | 12 | 15 | 27 | 12 / 27 = 44 % [25, 65] | 12 | 5 | 30 | 13 | 11 / 21 |

**U18 verdict.**
- Precision of unique: V1c 61 % > 44 %, so this criterion is met. Its interval is wide (18 answers), and
  V1's own precision at n = 100 is 55 %: seeds 50–99 are kinder to V1 than 0–49 were.
- **Rank-1 accuracy must not drop: it drops**, from 34 (V1) to 25 (V1c). Paired on the same seeds, V1c is right
  where V1 is not on 1 seed, and the reverse on 10 (exact sign test, p = 0.012). **U18 fails.** V1c is not
  adopted as a default; it stays a comparison arm.
- Answer rate: V1c 18 / 100, against V1's 38.
- Paired, V1c vs V1:
  - correct: 3 seeds V1c only, 13 V1 only (p = 0.021);
  - confident-wrong: 1 vs 8 (p = 0.039), so V1c is safer;
  - rank-1 error: median +10 m (n = 90, p = 0.057).

**What V1c does** (nothing tuned; all a-priori values):
- **The calibrated likelihood changes the ranking, not just the labels.** It loses 9 right rank-1 answers net.
  Discrimination failures rise from 25 to 30 and bias from 14 to 18.
- **The new statuses abstain heavily.** On V1c's 25 right answers: 11 unique, 10 inconsistent, 4 ambiguous.
  The χ² absolute fit flags 44 / 100 runs, 10 of them right. Every one of the 17 `locus` runs was wrong.
- V0c gives V0 a much lower confident-wrong count (18 → 6; 14 vs 2 discordant, p = 0.004), at no cost in rank-1
  accuracy (8 → 7), but it answers on only 10 runs.

**Likely causes (for the user; untested here):**
- The per-point ADS-B budget. Its latency term down-weights the CPA region, where the curve discriminates
  between cells and aircraft (step 2 findings above).
- The effective-N factor under-corrects for correlated ridge noise (the missed AR(1) test). Either would weaken
  discrimination.

Both are a-priori values or plan-specified forms, so changing them needs a new source (P3.1's covariance,
4.5 data), not a re-tune against this result.
