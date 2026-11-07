# A-priori registry of new knobs (reports/combined_plan.md P0.7)

Every knob added from Phase 0 onwards is listed here **before** the first measurement that uses it. Each
entry has a value, a source (physics / standard / literature / measured-in-data / arbitrary), a status, and
a fixed sensitivity grid. The values are also in config.yaml, with the same source in a comment.

**Rule.** A missed target is reported, never fixed by moving a provisional value. Moving a value needs a new
source (milestone 4.5 field data or physics), recorded here with the date. Reserved seeds (P4.1) add to
this rule; they don't replace it. CLAUDE.md applies too: provisional thresholds are never tuned against
synthetic data.

## Phase 0 (session 4, before the pilot and the Stage A baseline)

| Knob (config key) | Value | Source | Status | Sensitivity grid | Used by |
|---|---|---|---|---|---|
| `cascade.max_event_radius_m` | 12000 | physics: 2 × the largest audible slant (jet 6 km) | PROVISIONAL | 8000, 12000, 20000 | `cascade.build_event_index` |
| `cascade.weak_event_rel_sigma` | 0.5 in V1; null (off) in V0 | arbitrary | PROVISIONAL | 0.3, 0.5, 1.0 | `cascade.gating_events` (user F1: off in strict) |
| `cascade.max_partial_per_cell` | 16 | arbitrary | PROVISIONAL | 8, 16, 64 | `cascade.offset_filter` |
| `cascade.enum_budget_per_cell` | 5000 | arbitrary (algorithmic budget) | PROVISIONAL | 1000, 5000, 20000 | `cascade.offset_filter` |
| `score.inconsistent_rms_sigma` | 3.0 | statistics: 3σ convention | PROVISIONAL | 2, 3, 5 | `score.absolute_fit_status` (status only, never ranking) |
| `evaluate.success_radius_m` | 200 | user decision U10 (fixed before any result) | FIXED | — (report 150 alongside) | `evaluate.outcome` |
| `evaluate.confident_wrong_m` | 1000 | plan §3.0 metric definition | FIXED | — | `evaluate.outcome` |
| `evaluate.match_tol_s` | 10 | existing run.json truth matching (unchanged) | FIXED | — | `evaluate.match_events` |
| `evaluate.hop_ratio_tol` | 0.015 | arbitrary (diagnostic only) | PROVISIONAL | 0.01, 0.015, 0.03 | `evaluate.harmonic_hops` |

Unchanged existing caps used by the new enumeration: `score.max_hypotheses_per_cell` 64 (now the fully
matched cap; partial hypotheses have their own cap above).

## Phase 1: P1.1 calibrated likelihood and P1.4 ambiguity v2 (session 5, before the U18 measurement)

Registered before any measurement that uses them (unit tests on synthetic ridges excepted, which check the
implementation, not a target). They act only in the comparison arms V1c / V0c (`score.likelihood: calibrated`,
`score.ambiguity_mode: v2`); V0 / V1 are unchanged. The plan names some of these `ambiguity.*` and
`score.adsb_budget`; there is no `ambiguity` config section, so they live under `score` with the names below.

| Knob (config key) | Value | Source | Status | Sensitivity grid | Used by |
|---|---|---|---|---|---|
| `score.likelihood` | huber (V0, V1); calibrated (V1c, V0c) | plan P1.1 / U1 (arm only) | mode, not a threshold | — | `score._hyp_cost` |
| `score.outlier_frac` (ε) | 0.05 | arbitrary (plan P0.7 table) | PROVISIONAL | 0.01, 0.05, 0.2 | `score._event_nll` |
| `score.adsb_pos_m` | 7 | DO-260B NACp class (plan P0.7 `score.adsb_budget`) | PROVISIONAL | ×0.5, ×1, ×2 (jointly with the two below) | `score._ridge_sigma2` |
| `score.adsb_speed_mps` | 0.26 | 1 kt quantization / 2 (plan P0.7) | PROVISIONAL | ×0.5, ×1, ×2 | `score._ridge_sigma2` |
| `score.adsb_latency_s` | 0.5 | OpenSky latency literature (plan P0.7) | PROVISIONAL | ×0.5, ×1, ×2 | `score._ridge_sigma2` |
| `score.sigma_inflation_cap` | 3 | plan P1.1 ("capped at 3×"); arbitrary | PROVISIONAL | 2, 3, 5 | `score._event_nll` |
| `score.ambiguity_mode` | v1 (V0, V1); v2 (V1c, V0c) | plan P1.4 (S7, delegated) | mode | — | `cli.run_locate` |
| `score.tie_quantile` | 0.95 (2·Δnll ≤ χ²₂ → Δnll ≤ 3.00 nats) | statistics (plan P0.7 `ambiguity.tie_quantile`) | PROVISIONAL | 0.9, 0.95, 0.99 | `score.ambiguity_v2` |
| `score.gof_quantile` | 0.99 per event | statistics (plan P0.7 `ambiguity.gof_quantile`) | PROVISIONAL | 0.95, 0.99, 0.999 | `score.gof_status` |
| `score.ambiguity_radius_m` (existing) | 400 | unchanged (session 3) | PROVISIONAL | — | `score.ambiguity_v2` |

Implementation choices inside P1.1 (no new knob, recorded so they are not mistaken for tuning):
- **Effective N.** The plan says "from the residual autocorrelation (`fit.correlation_factor`)". Taking it from
  each hypothesis' own residuals would reward bad fits: systematic misfit makes residuals correlated, so a
  larger κ divides the cost more. κ is therefore hypothesis-independent: (1 + ρ₁)/(1 − ρ₁) with ρ₁ the AR(1)
  lag-1 correlation of the decimated ridge, solved model-free in `score.ridge_white_stats` (the same estimator
  as P0.4's σ_ref; ρ₁ ≤ 0.95). The characterization fit's own factor is on raw, overlapping spectrogram
  points, not on the scorer's decimated points.
- **σ_e** is the hypothesis' residual RMS kept within [σ_white, 3·σ_white] (plan). The per-point term is a
  normalised density, so a larger σ pays ln σ; this is a profile likelihood, not a free inflation.
- **ADS-B budget mapped to frequency:** speed error → f·δv/c; latency and the along-track part of the position
  error (δp / v, v = the event's fitted speed) → ḟ·δt. Cross-track position error is neglected (second order).
- **B** (outlier density width) is the ridge search band from the ridge extractor (`ridge.band_hz`).
- **Unmatched event** (V1c): every point costs −ln(ε/B) (κ = 1). Ranking stays lexicographic (U8).
- **Silence** (V1c): the probabilistic-silence penalty is added in nats, not per point.
- **f0** is profiled as in the Huber loss (IRLS, k = `huber_k`, at σ_white), not re-maximised under the mixture.

## U19 detection arms (session 6, plan §8.2; before the detection screen)

Registered before the first measurement (the session-6 screen). Off by default, so V0 / V1 are unchanged; on only
in the detection comparison. `detect.trigger_db`, `extend_db`, `min_duration_s` and every other existing
threshold are untouched: the arms only add rows to the max-over-rows statistic.

| Knob (config key) | Value when on | Source | Status | Sensitivity grid | Used by |
|---|---|---|---|---|---|
| `detect.harmonic_stat` | true (arm a) | plan §8.2 | mode | — | `detect.detect_events_audio` |
| `detect.harmonic_n` | 3 | as `ridge.hough_harmonics` (the seeding matched filter's harmonic count) | PROVISIONAL | 2, 3, 5 | `detect.harmonic_statistic` |
| `detect.long_window_s` | 3.0 (arm b; **default since U19**, session 8) | plan §8.2 "2–4 s" (the doppler.txt resolution step); midpoint | PROVISIONAL until milestone 4.5: re-check on field recordings, above all spurious events from stationary lines (hum) and wind | 2, 3, 4 | `detect.detect_events_audio` |
| `detect.low_band_hz` | [20, 100] (arm c) | upper edge: the recording high-pass corner (`realism` scene, 100 Hz); lower edge: the conventional lower limit of audio | PROVISIONAL | lower edge 20, 30, 40 | `detect.DetectParams.prop_bands` |
| `synth.scenario_detection` | `{detect: {long_window_s: null}}` | not a method knob: it fixes the benchmark. The scenario builder places cascade decoys from detected events, so without a pin a detection-default change also changes the scenarios. Pinned to the detector Stage A / U18 / U19 scenarios were built with (user decision, session 8) | FIXED (benchmark definition; change only by a recorded re-baseline) | — | `cli.build_synth_scenario` |

Implementation choices (no knob):
- **Arm a.** Per frame, the max over fundamentals f in [lo, hi/n] of the mean excess at f, 2f, …, n·f. Each harmonic
  takes the max of the 3 nearest bins, for bin quantization, as `ridge.hough_dilate_bins` does. Its noise
  baseline is the mean of the same statistic on exponential noise (10·log10(E/ln 2)), by a fixed-seed Monte Carlo
  on the band's own bin layout: the harmonic analogue of `theoretical_baseline_db`.
- **Arm b.** The prop bands' plain tonal statistic on a spectrogram with the long window (same overlap). The
  coarser frame grid is sampled at each main frame's nearest long frame.
- **Arm c.** The low band is one more row of the main spectrogram's tonal statistic, so it gets its own
  (smaller-N) noise baseline. The ridge search band (`detect.band_hz`) is unchanged.

## U19 task 4: arm h, the seed-to-ridge hand-off (session 8; before the hand-off screen)

Registered before the first measurement of the arm. The diagnostic that motivates it (`reports/u19.md`, "Task 4,
step 1") measured no arm. Approved by the user (session 8). Off by default, so V0 / V1 are unchanged. Both knobs
are modes; neither adds a number. `ridge.guide_hz`, `ridge.weak_db`, `ridge.min_points` and every other threshold
are untouched.

| Knob (config key) | Value when on | Source | Status | Sensitivity grid | Used by |
|---|---|---|---|---|---|
| `ridge.handoff_harmonics` | true (arms h1, h12) | step-1 diagnostic: on short guided ridges whose seed is on a missed event's line, the seed line holds a median 11 strong columns and its 2nd / 3rd harmonics 258 / 120. Harmonics tried: m = 2 … `ridge.hough_harmonics` (3), those the seed statistic already sums | mode | — | `fit.characterize_window` |
| `ridge.handoff_continue` | true (arms h2, h12), prop band only (`ridge_jet: false`) | step-1 diagnostic: 16 missed events lost to the `break` after a seed on another line. The peel stays bounded by `max_ridges + 2` attempts | mode | — | `fit.characterize_window` |

Implementation choices (no knob):
- **h1.** On a short guided ridge, `guided_ridge` runs around m × the seed curve for each m. The ridge with the
  most points is kept, ties going to the lower m. The fit, the guided passes, the descent check and the masking
  (the fitted curve's family and the seed curve's family) are the same as for a ridge on the seed line. The jet
  band has `hough_harmonics: 1`, so h1 cannot act there.
- **h2.** When no ridge results, the seed curve's harmonic family is masked (±`mask_halfwidth_hz`) and the peel
  continues with the next seed, instead of leaving the window.
- **Screen** (rules arms out only): h1, h2 and h12 against the current default (arm b on), six configs (clean,
  all-on, loo:path, loo:levels, loo:scene, sub:reflection), seeds 0–49, paired by audio.
  - Measures: recall, spurious events per run, extra fits within `evaluate.match_tol_s` of a truth CPA, the
    matched fits' |Δv| / v and |Δd| / d, and detection time.
  - Paired exact sign test on truth events per config.

### Arm h1g: the hand-off plausibility guard (session 8; registered before its first measurement)

The arm h screen (`reports/u19.md`, "Task 4, step 2") left h1 not ruled out on recall (pooled 49 gained / 0
lost) but ruled out as built on spurious fits (pooled 129 → 250, all-on 21 → 68). About a third of the extra
all-on spurious fits were physically implausible. Approved by the user (session 8, option 1). Off by default.

| Knob (config key) | Value when on | Source | Status | Sensitivity grid | Used by |
|---|---|---|---|---|---|
| `ridge.handoff_guard` | true (arm h1g = `handoff_harmonics` + `handoff_guard`) | the arm h screen's spurious breakdown. The guard reuses existing values only: the fit's received CPA inside the detection window, `v` inside `ridge.hough_v_range_mps` (35–260 m/s), `d / v` inside `ridge.hough_tau_range_s` (2–80 s), the ranges the Hough seed already searches | mode | — | `fit.characterize_window` (`fit.handoff_plausible`) |

Implementation choices (no knob): the guard acts only on fits whose ridge came from the h1 hand-off, after the
fit and its guided passes. A rejected hand-off fit is treated as no hand-off (the peel leaves the window, as in
the base). No fit on the seed line itself is checked, so with h1 off the guard changes nothing.

Screens (rule arms out only; neither is a decision run):
1. **Detection**, exactly as the arm h screen: base, h1, h1g; six configs × seeds 0–49, paired by audio
   (`handoff_screen.py`). h1g is ruled out if it keeps fewer than half of h1's pooled gained events, or if its
   pooled spurious count is not below h1's.
2. **End to end**, V1 with h1g (variant `V1h1g`) on all-on C seeds 0–49 (`cli.py ablate --plan stageA --limit 50`),
   paired against the Stage A2 V1 rows on the same units. Measures: rank-1 accuracy (exact sign test), recall,
   spurious events, rank-1 error and true-cell rank (Wilcoxon), timeouts. h1g is ruled out if rank-1 runs lost
   exceed runs gained. Not ruled out is not acceptance: a default change needs its own pre-registered decision
   run.

## Two-stage refinement (session 9; registered before any measurement)

A result-changing speed-up, so an arm (CONTEXT.md rule 2). Approved by the user (session 9): K = 50 and the
decision rule below. Off by default (`score.refine_lbfgs_top_k: null`), so V0 / V1 are unchanged; the golden
rows (tests/test_golden_rows.py) and the frozen scoring oracle confirm it.

| Knob (config key) | Value when on | Source | Status | Sensitivity grid | Used by |
|---|---|---|---|---|---|
| `score.refine_lbfgs_top_k` | 50 (arm V1k; default null = every refined cell) | 10 % of `refine_max_cells` (500) and 2.5 × `viewer.max_candidates` (20): a margin over the displayed candidates and the 15 % ambiguity tie set. Fixed before measuring, not tuned | a-priori | — | `score.rank_cells` |

Implementation choices (no knob):
- The `refine_max_cells` cells chosen as now (centre score) all get the 3 × 3 × 13 start grid (`score.refine_start`).
- The best K of them by (unmatched count, start-grid pooled cost + silence penalty), the score cells are ranked
  on, also get L-BFGS-B, started from the same grid (`refine_in_cell(start=...)`).
- The others are scored at their best start-grid point, never worse than the centre.

What can change: only units with more than K refined cells. In the Stage A2 V1 rows that is 249 of 275 units
(177 hit the 500-cell cap).

**Decision run.** `cli.py ablate --plan stageA --variants V1k --jobs 8` from clean main on the compute machine
(the Stage A2 machine), all 275 units, paired by unit against the Stage A2 V1 rows
(`reports/data/stageA2_rows.jsonl.gz`). Smoke run first (CONTRIBUTING §4.1). Measures and verdict:
`reports/experiments/two_stage_decision.py`, written before the run. **V1k is accepted only if D1, D2 and D3
all hold:**
- **D1, rank-1 accuracy does not drop.** Rank-1 accuracy ignoring status (`evaluate.rank1_ok`) over the 275
  units ≥ A2 V1's 107. Paired gained / lost and the exact sign test are reported.
- **D2, confident-wrong does not rise.** Units with outcome `confident_wrong` ≤ A2 V1's 17.
- **D3, it is clearly faster.** On the units with more than K candidates in A2 that finish in both runs, the
  median per-unit ratio of A2 scoring time to V1k scoring time (`timings_s.score_s`) is ≥ 2.
- Reported, not gating: timeouts (A2 V1: 3), correct outcomes (62), recall (unchanged by construction:
  detection is identical), rank-1 error and true-cell rank (Wilcoxon), total wall time.
- Known cost, as for U19's C3: a timeout flip is wall-clock noise and can move D1 or D2 by a unit.

**Outcome (session 9): REJECTED.** D1 107 → 107 and D2 17 → 17 pass; D3 fails (median ratio 1.57, below 2).
`refine_lbfgs_top_k` stays null. Details: `reports/two_stage.md`; rows: `reports/data/two_stage_rows.jsonl.gz`.

## Later phases

These entries are listed in plan P0.7 and are added to config.yaml only when their item is built:
`score.outlier_frac`, `score.adsb_budget`, `ambiguity.tie_quantile`, `ambiguity.gof_quantile`,
`score.top_assignments`, `score.p_unmatched`, `tracks.max_bridge_s`, `tracks.baro_radius_km`,
`silence.source_level_db`, `silence.null_shifts_s`, `uncertainty.nuisance_sigma`.
