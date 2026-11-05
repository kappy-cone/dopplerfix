# Combined plan: our pipeline + the doppler.txt methodology (revision 2)

Session 4, design and planning only. No repo file was changed except this report.

Inputs, in the order read: CLAUDE.md, HANDOFF.md, doppler.txt, the three skeptic reviews, the code
(doppler/*.py, cli.py, config.yaml), then reports/doppler_comparison.md. Revision 2 adds
reports/plan_review.md, an adversarial review of revision 1. Every point in it was checked before it was
accepted; §6 (Revision log) records how each was resolved.

**Citation keys**
- `[P-Fn]`: reports/skeptic_ours_physics_scoring.md, finding n.
- `[S-Fn]`: reports/skeptic_ours_system_validation.md, finding n. `[S-1a]` is its realistic sweep table.
- `[T-Fn]`: reports/skeptic_theirs_doppler_txt.md, finding n.
- `[C-#n]`: reports/doppler_comparison.md, change n.
- `[R-Mn]` / `[R §n]`: reports/plan_review.md.
- `Lnnn`: doppler.txt line.
- `file:function`: our code.
- `[D-n]`: my re-checks, in
  `<scratch>/designer/`.
  Run them with `cd <repo> && PYTHONPATH=. .venv/bin/python <script>`.

**Variant names**
- **V0-asdesigned / V1-asdesigned.** Strict (`max_unmatched_events: 0`, `silence_audibility: hard`) and
  robust (the current default), exactly as at commit ea8fe0d. Pinned and run once on the baseline seeds
  (P0.0), so the designs the user asked to keep are always on record.
- **V0′ / V1′.** The same configurations on bug-fixed code (P0.1).
- **Ladder rungs R1–R4.** Incremental changes on top of V1′ (Phase 2).
- **V2.** The highest rung that passes its paired acceptance test. V2 is an outcome, not a bundle.

---

## 0. Re-checks (what I measured myself)

| ID | Script | Question | Result |
|---|---|---|---|
| D-1 | `d1_unmatched_evidence.py` (reuses the skeptic's E1 generator) | [S-F1]: when event 2's aircraft has no ADS-B, does the wrong full-match rank 1 fit badly? | K = 60 s1, K = 120 s0, K = 120 s1. The wrong full match, 7–9 km away, has **rms/σ = 7.5, 4.9, 7.4 on its worst event**. The best partial hypothesis fits at rms/σ ≈ 0.5, but it is **also wrong** (9.5, 7.7, 7.1 km away: a single-event locus on a background track). My script's "PARTIAL(true side)" label was hard-coded, not computed. The per-point prices tested (2, 4.5, 22.5) are *not* the null model revision 1 proposed. **What D-1 supports:** on clean ridges, pricing unmatched events or an absolute-fit check turns a confident wrong answer into an *uninformative* one (locus / ambiguous / inconsistent). It does **not** show recovery of the truth. |
| D-2A | `d2_bias_nuisance.py` | [P-F4]: Newton seed at segment ends | 6/10 points marked invalid; all 6 are false (emission 88–96 s, inside a track that ends at 100 s). The reviewer reproduced this. |
| D-2B | same | [P-F2]: corridor amplification of ADS-B latency | ±0.6 s gives **397 m** same-direction, 46 m crossing, 39 m reciprocal. The analytic lever for parallel tracks is 1.2 s / \|1/v₁ − 1/v₂\| = 504 m. |
| D-2C | same | Linear f0 drift in scoring vs an RPM ramp of 3 %/20 s at CPA + 5 s | Bias 257 → 125 m (crossing), 157 → 66 m (reciprocal), 702 → 635 m (corridor). Clean CRLB unchanged (2.2 → 2.4 m). The reviewer's `r1_drift_prior.py` shows the result is independent of the prior σ_β from 1e-5 to 1e-2 /s; it reverts to 256 m only at 1e-7. Caveats: one ramp shape; residual reduced χ² still ≈ 2.9 after drift. |
| D-2D | same | Linearised systematic-aware covariance (latency 0.5 s, altitude 60 m per track) | 1σ ellipses 73 × 29, 262 × 51, 54 × 23 m, against noise-only 2.2, 9.4, 1.5 m. These σs equal the realism layer's own draws, so synthetic coverage is near-guaranteed (see P1.3). |
| D-3 | `d3_dedupe.py` | [S-F12]: how often does `_simple_ratio(tol 0.03, max_den 4)` accept unrelated props (f0 drawn from 80–180 Hz)? | 44.8 %. This is a **conditional** rate: a merge also needs CPA within 3 s and v/d within 15 %/35 %, so the real false-merge rate is far lower [R §2]. |
| **D-4** (new) | `d4_absfit_realistic.py` | [R-M2]: on the *realistic* sweep (S-1a run directories), does an absolute per-event RMS test separate right rank 1s from wrong ones? | **Correct rank 1s** (C3, C5, C7): worst-event rms **1.04, 1.46, 0.68 Hz**. **Confidently wrong:** C1 (4.9 km) **0.63/0.30 Hz**, *the best fit of all*; C6 (3.0 km) 1.61 Hz; C4 (4.8 km) 10.3 Hz; C0-strict (1.15 km) 6.7 Hz; A0 (right tracks, 407 m, garbage event matched) 3.49 Hz. True-cell rms in C0 is **2.13/3.04 Hz** (an RPM ramp). **Finding:** an absolute test (e.g. > 2 Hz) catches 3 of the 5 >1 km confident-wrong answers (C4, C0-strict, and A0's garbage match). It misses C1, which is closer than C5's correct answer, and C6. It also flags the right assignment in C0. Useful and cheap, not sufficient. |
| **D-5** (new, arithmetic) | the numbers above + [P-F3] fit σ | [R-M1 problem 3]: under realism, does revision 1's P2.3 null (free straight-fit nats + ½·4·ln n_eff − ln p_u ≈ 10–12 nats) leave *true* events unmatched? | C0 true cell: event 2 locked rms 3.04 Hz vs its free-fit scatter 0.19 Hz (gof ≈ 1, so free rms ≈ σ). That is an RSS ratio ≈ 256: ≥ 100 nats at any n_eff ≥ 40, against a price of about 12. Even the no-ramp counterfactual (0.75 vs 0.19 Hz, ratio 15.6) costs ≈ 1.4·n_eff nats. **Confirmed: as specified, P2.3 would turn true matches into "unmatched".** The free straight fit absorbs ramps and ADS-B error that a real track model can't. Revision 2 drops the free-fit null (Phase 2, R3). |

Everything else that I call confirmed is confirmed by reading the code at the cited lines.

---

## 1. Strengths and weaknesses, stage by stage

*Ours* is our repo; *theirs* is the transcript, which is narration only, so "not described" is not "not
done". *Net* is what to import or fix.

### 1.1 Event detection
- **Ours.**
  - **+** Automated tonal detection with a theory-floored baseline, chunked for any clip length
    (`detect.py`).
  - **+** A straight-pass matched-filter seed (`ridge.doppler_hough`).
  - **−** Every detection enters the cascade. A0 had a car-like source at 26.9 m/s and a garbage event at
    216 ± 524 m/s [S-1a]. `max_unmatched_events` tolerates exactly one [S-F6].
  - **−** Synthetic events are resampled to ≥ 0 dB, so below 0 dB is never tested [C §3.4.5].
- **Theirs.**
  - **+** Human curation (L67–74).
  - **+** Long-window zoom finds sub-audible curves (L45–49).
  - **−** The criteria aren't stated, and the curation is not blind [T-F1].
- **Net.**
  - Keep automation.
  - Handle spurious events in ranking and status (Phase 2, R3), not by filtering.
  - Event lists become a stage I/O file, a seam only (P0.3).

### 1.2 Curve measurement (ridge)
- **Ours.**
  - **+** Ridge points with weights; harmonic masking.
  - **−** The scoring ridge is re-extracted within ±3 Hz of the *straight* fit (`fit.characterize_window` →
    `ridge.guided_ridge`) [P-F9].
  - **−** Twin detune and reflection notches make the peak wander (SUSPECTED) [P-F14].
- **Theirs.**
  - **+** Brightness along the modelled curve (L520–523), with no ridge extraction: tolerant of gaps and
    faint segments.
  - **−** No noise model, no normalisation, no null; hum lines inflate scores [T-F3].
- **Net.** A hypothesis-consistent comb-power score with a per-event floor and a time-shift null: as a
  second opinion (P3.4) and inside silence (R4b).

### 1.3 Per-event characterization
- **Ours.**
  - **+** Robust fit; honest covariance for correlated noise and clipped passes [P-F11].
  - **−** Under realism the covariance is overconfident. 11/16 events have |Δd| > 3σ and 9/16 have
    |Δv| > 3σ [S-F2, S-F10].
  - **−** Ramps bias the fit by 3–10σ [P-F1].
  - **−** `gof` ≈ 1 by construction (`fit.fit_ridge` l.168 vs l.208) [P-F3].
- **Theirs.**
  - **−** Slider fits with "generous ranges" (L279–298) [T-F7].
- **Net.**
  - Keep t_rx and the shared-Δt test as the hard gates: t_rx errors stayed ≤ 0.47 of tolerance [S-F10].
  - Restructure v/d gating after the census (P3.5).

### 1.4 Flight data
- **Ours.**
  - **+** `lastposupdate`, outlier removal, geoid grids, cropped zstd Parquet.
  - **−** PCHIP through noisy fixes.
  - **−** All-or-nothing velocity fallback (`tracks.tracks_from_frame`) [P-F8]. Never exercised
    synthetically: `realism.degrade_adsb` interpolates velocity for every fix [R-M3].
  - **−** Baro altitude treated as ellipsoidal height [S-F11].
  - **−** Gaps > 20 s split passes [S-F5].
  - **−** UAT-only GA untested; events are protected from drops (realism.py:285) [S-F8].
- **Theirs.**
  - **+** Whole-day CONUS scrape; keeps unknown types (L88–119).
  - **−** Altitude datum wrong (L173–174) [T-F5]; no timing model [T-F8].
- **Net.**
  - Smoother with covariance, in ECEF (P3.1).
  - Pressure correction from dual-reporting aircraft (P3.2).
  - The velocity fix before milestone 7 (H-c).
  - UAT coverage measured early (P5.3, scheduled alongside Phase 1).

### 1.5 Cascade (CPA prefilter, offset)
- **Ours.**
  - **+** Exact shared-Δt ∩∪ offset filter for N events, vectorised [S §4].
  - **+** Linear grid term; KD-tree tiling.
  - **−** `wind_sys_mps: 15` is about 40× physical wind; it absorbs fit bias and is still not enough
    [P-F10, S-F10].
  - **−** Tile pruning ignores `max_unmatched_events` (`cascade.prepare_cascade` l.642–647) [S-F3].
  - **−** Enumeration capped in insertion order, O(product) [S-F6].
  - **−** Very large CPA radii for garbage events. A0 spent **157–162 s in the CPA stage itself**
    (run.json `cascade_cpa_s`), C2 79 s, C6 21 s. I checked: `run_tile`'s `sec["cpa"]` covers
    `cpa_prefilter` only. The review attributed this to enumeration.
  - **−** Robust = locus generator [S-F7].
- **Theirs.**
  - **+** The same structure, with counts at each stage (L121–326).
  - **−** Intuitive tolerances [T-F7].
- **Net.**
  - Fix pruning, enumeration and the radius cap in Phase 0 (P0.1).
  - Tolerances after the census (P3.5).

### 1.6 Silence
- **Ours.**
  - **+** Whole clip, minimum audible duration, the CPA explanation rule, a probabilistic option.
  - **−** The explanation leaks across hypotheses; computed per cell at the interim-best Δt
    (`cascade.run_tile` l.666–682) [S-F4, P-F6].
  - **−** Gaps and slow aircraft are punished twice [S-F5].
  - **−** Hard slant audibility killed the true cell in 4/8 realistic seeds [S-F2].
  - **−** Probabilistic silence is nearly inert [S-F7].
  - **−** Synthetic placement is circular [S-F9.1].
- **Theirs.**
  - **+** "What we don't hear" is signal (L326–338), and it did most of their pruning.
  - **+** The window between events is the most believable non-detection interval [T §3].
  - **−** "Conservative" radius = aggressive silence [T-F4].
- **Net.**
  - Per-hypothesis silence and undetermined passes (R2).
  - An audibility ladder: measured floor (R4a), then forced detection (R4b).
  - A between-events window as a cheap baseline toggle (P0.4).

### 1.7 Curve modelling
- **Ours.**
  - **+** Emission-time radial velocity, Newton retarded time (`score.modeled_ratio`) [P §3].
  - **−** The seed bug [P-F4, D-2A]; one segment per hypothesis.
- **Theirs.**
  - **+** The same model, well explained (L476–506).
  - **+** "Model the actual geometry" (L405–418).
- **Net.** Fix the seed (P0.1-a); gap bridging (R2 minimal, then P3.1).

### 1.8 Scoring and cost
- **Ours.**
  - **+** Locked N-event scoring (one Δt, per-event f0, timing only from physics), the strongest element on
    either side [P §3, T §3].
  - **−** σ is straight-line misfit; points are correlated (over-counted 3–6×); no ADS-B model error
    [P-F3].
  - **−** The unmodellable penalty sits below a large residual [P-F5].
  - **−** Constant f0 under ramps [P-F1].
  - **−** Not a log-likelihood, so silence nats and unmatched events can't be priced [P-F6, P-F7].
- **Theirs.**
  - **+** Locked scoring (L555–575); bin-width tolerance to sub-Hz biases [C §3.2].
  - **−** No null and multiple comparisons [T-F3].
- **Net.**
  - Calibrated likelihood (P1.1).
  - Drift only via ablation and a USER decision (P3.3).
  - Comb power as a second opinion (P3.4).

### 1.9 Ranking and grouping
- **Ours.**
  - **−** The ranking unit is the cell, so ranks 5–39 can be one valley [P §2.5].
  - **−** Lexicographic ranking gives confident wrong answers when one event lacks ADS-B (4/9 "unique" and
    wrong at K ≥ 60) [S-F1].
  - **−** Fit quality does not separate right from wrong under realism: wrong C1 fits *better* than the
    correct C5 [D-4].
- **Theirs.**
  - **+** "Best point per flight pair" (L606–613); runner-up overlays (L623–639); a per-event score
    scatter.
- **Net.**
  - Assignment-level ranking (P1.2).
  - Unmatched-event ranking measured before it is built (R3).
  - Absolute fit as a *status*, not a ranking rule, from Phase 0 (P0.4).

### 1.10 Refinement
- **Ours.**
  - **+** Continuous in-cell L-BFGS-B.
  - **−** Bounded per cell; mic height fixed at the cell-centre DEM [S-F14].
- **Theirs.**
  - **+** Finer resolution once the pair is known (L642–648).
  - **−** 100 m re-grid quantization [T-F13].
- **Net.** Per-assignment refinement over its cells (P1.2).

### 1.11 Confidence and ambiguity
- **Ours.**
  - **−** The 15 %-relative tie set is scale-dependent and restricted to the same n_unmatched
    (`score.ambiguity` l.356).
  - **−** It never abstained in 8 realistic runs [S-1a, P-F12, S-F16].
- **Theirs.**
  - **−** A visual margin with no null [T-F3]; a non-independent "confirmation" [T-F2].
  - **+** The posterior-predictive idea.
- **Net.**
  - Ambiguity v2 with absolute and prior-edge statuses (P1.4).
  - An ellipse, reported per group with its known blind spots (P1.3).

### 1.12 Validation
- **Ours.**
  - **+** Automated, recorded, realism layer.
  - **−** Circular in specific ways [S-F9].
  - **−** n = 1 per group (3/3 allows a 37 % true rate, one-sided 95 %).
  - **−** The rank metric hides confident wrong answers.
- **Theirs.**
  - **−** n = 1, not blind, tuned in a loop (L369–373) [T-F1].
  - **+** Blind visual confirmation is real evidence.
- **Net.** A paired, powered baseline (P0.6); an a-priori knob registry (P0.7); Phase 4 with generator
  models that differ from the pipeline's (P4.2).

### 1.13 Scale
- **Ours.**
  - **+** Streaming and pruning.
  - **−** Dense TrackSet; Python loops; the robust variant's locus growth [S-F6, S-F7].
- **Theirs.**
  - **+** Shows a CONUS cascade is feasible (73k → 15k → 30–40k scored; L316–589).
- **Net.** P5.1–P5.2 with a storage budget.

### 1.14 Automation and HITL
- **Ours.**
  - **+** Pure stage functions.
  - **−** `cmd_locate` is ~380 lines (`cli.py:428`).
  - **−** `locate` can't consume saved events.
  - **−** `manual_inputs` is incomplete [S-F15].
- **Theirs.** Everything is manual. [T §3] separates genuine analyst value (curation, faint tracing, visual
  confirmation) from steps that are just not automated.
- **Net.** Stage-function refactor with stage I/O seams (P0.3); full run.json provenance (P0.1-h). No HITL
  tooling.

---

## 2. Adjudication of every critical and major finding

| Finding | Sev. | Verdict | Reason | Plan item |
|---|---|---|---|---|
| P-F1 RPM ramp 240–700 m | crit | ACCEPT (size); NEEDS-TEST (prevalence) | D-2C reproduces 257 m. Governed props are within ~1 %. | P3.3, P5.4 |
| P-F2 position–Δt degeneracy | crit | ACCEPT | D-2B; it is a geometry limit, so honest uncertainty, not a fit. | P1.3, P1.4 |
| P-F3 σ ≠ noise; gof no-op; correlated points | major | ACCEPT | Code-read. Location impact small [P-F3]; it blocks calibrated statuses. | P1.1 |
| P-F4 Newton seed | major | ACCEPT | D-2A. | P0.1-a |
| P-F5 unmodellable price | major | ACCEPT | huber(12, 3) = 31.5 > 22.5. | P1.1 |
| P-F6 silence nats; per cell | major | ACCEPT | Code-read. | P1.1, R2 |
| P-F7 lexicographic | major | ACCEPT (mechanism) | D-1 confirms on clean ridges; D-4/D-5 show the obvious fixes fail under realism. | R3 (measure first), P1.4 |
| P-F8 PCHIP / velocity fallback | major (real) | ACCEPT | Code-read. Not exercised synthetically [R-M3]. | H-c, P3.1 |
| P-F9 ridge guided by the straight fit | major | NEEDS-TEST | Size unmeasured. | P3.4 |
| P-F10 wind allowance masks F1 | minor/major | ACCEPT | Agrees with S-F10. | P3.5 |
| P-F12 ambiguity scale | minor–major | ACCEPT | Code-read; S-1a. | P1.4 |
| P-F14 twin / reflection | minor–major | NEEDS-TEST | The generator's reflection model is itself questionable (R = +0.8–1.0 real; real ground → −1 with phase) [R §4]. | P0.6 (path group marked provisional), P4.2 |
| S-F1 confident wrong, missing ADS-B | crit | ACCEPT | E1 + D-1. Only a status fix is shown to work (D-1); ranking fixes need measurement (D-5). | P1.4, R3 |
| S-F2 realistic sweep | crit | ACCEPT with caveat | n = 8, one scenario type, `n_near_traffic` = 4 fixed. "Robust 3/8 correct" has a one-sided 95 % lower bound of 0.11. | P0.6 |
| S-F3 tile pruning | major | ACCEPT | cascade.py l.642–647. | P0.1-b |
| S-F4 silence leak | major | ACCEPT | Code-read. | R2 |
| S-F5 gaps / slow aircraft double-counted | major | ACCEPT | Code-read; E4/E5. | R2, P3.1 |
| S-F6 enumeration | major | ACCEPT | C2: 7488 hypotheses / 502 s; C6: 14,606 / 432 s. The condition revision 1 set for moving it forward is met. | **P0.1-i (Phase 0)** |
| S-F7 locus generator at US scale | major | ACCEPT (direction); NEEDS-TEST (magnitude) | — | P5.1, P5.2 |
| S-F8 UAT coverage | major | NEEDS-TEST | Decides p_u and V0 viability. | P5.3, started alongside Phase 1 |
| S-F9 circularity | major | ACCEPT | Code-read. The *new* items must not re-create it [R-M7]. | P4.2, P1.3, R4 |
| S-F10 uncalibrated tolerances | major | ACCEPT | S-1a census. | P3.5 |
| S-F11 baro altitude | minor–major | ACCEPT | — | P3.2 |
| S-F12 dedupe | minor | ACCEPT (mechanism); low priority | D-3 is conditional [R §2]. | R2 (option, gated on recall) |
| S-F13 window / relative modes | minor | ACCEPT; **deferred** | The benchmark is known time; a day-long window ≈ 44 GB dense. | after P5.1 |
| S-F14 DEM untested / slope / undeclared nodata | minor | ACCEPT; partially scheduled | Counting goes in H-d; slope and nodata-value checks in the P4.4 DEM sweep; terrain shielding deferred (non-goal: terrain acoustic modelling). | H-d, P4.4 |
| T-F1 their n = 1 | crit | ACCEPT | Lesson imported as protocol. | P0.7, P4.1 |
| T-F2 single event not independent | high | ACCEPT | — | P1.4, U7 |
| T-F3 brightness uncalibrated | high | ACCEPT | The import adds a floor and a null. | P3.4, R4b |
| T-F4 silence / missing tracks | high | ACCEPT | — | R2, R4 |
| T-F5 datum | medium | ACCEPT (ours already right except baro) | — | P3.2 |
| T-F6 fixed c | medium | ACCEPT for them; low priority for us, but asked (U3) | — | U3 |
| T-F8 ADS-B timing | medium | ACCEPT | — | P1.3, P3.1 |
| T-F11 countermeasures | medium | ACCEPT | Milestone 8 reruns localisation against SNR. | M8 |
| T-F12 dense traffic | medium | ACCEPT | — | P4.2 |
| T-F13 precision unmeasured | medium | ACCEPT | — | P4.1 metrics |

### Where I differ from doppler_comparison.md (resolved on evidence)
1. **[C-#3] σ_model as "the best single bet for the gap": downgraded.** It is for calibration, not location
   (run C: 14–17 m [P-F3]).
2. **[C-#4] global c/wind: low priority.** Single-effect 4–43 m [P-F10]. However, [P §2] shows a 2.4 m/s c
   change flips which side of a degenerate valley wins, so single-effect sizes don't settle it in poor
   geometry [R §2]. It is now a USER question (U3) with a recommendation to defer until the paired c/wind
   LOO subgroup result.
3. **[C-#13] "~40 m per track" latency: underestimated.** It is 397–468 m in corridors (D-2B). Report-only
   marginalisation (P1.3); not fitted.
4. **[C §3.1] "strict works when events are trustworthy".** The *mechanism* that kills V0 is confirmed:
   slant-only audibility rejects for inaudible aircraft, and the hard d gate rejects at CPA; all five
   two-event seeds lost the true cell, 4 to silence and 1 to the d gate. *How often* it happens on real
   data is **NEEDS-TEST**: S-1a always places 4 near-traffic aircraft at 0.5–1.5× threshold [R-M7]. It is
   measured under traffic-model placement (P4.2) and real replay (P4.3).
5. **[C-#9] confidence-tiered anchors: a pruning accelerator only** (P5.1). Anchors are chosen from an
   overconfident covariance [S-F2].
6. **[C-#10] noise-floor silence: accepted, staged** as R4a (measured floor) before R4b (forced detection).
7. **[C-#8] per-pass kinematic tolerance from the ADS-B speed spread around CPA: accepted** into P3.5 as a
   data-derived term [R §4]. It covers aircraft kinematics (turns, speed changes), not source RPM changes.
8. **[C-#1], [C-#2], [C-#6], [C-#7], [C-#12] accepted** (P0.4, H-d, P3.3, P3.4, P1.2). **[C-#5]
   NEEDS-TEST** (hop count in P0.4).

---

## 3. The combined plan

### 3.0 Measurement standard (applies to every item)

**Design.** A *paired* leave-one-group-out (LOO) ablation. The same seeds are used across every
configuration and every arm (P0.2 makes that valid).
- **Scenarios.** C (60 s, 15/45 s; the benchmark format) is primary. A (600 s, 150/420 s) and G (300 s, 3
  events) are secondary.
- **Configurations.** all-on; LOO of each group (adsb, levels, source, path, scene, geometry); LOO
  subgroups (RPM, c/wind, mic height); clean.
- **Seed allocation, for power.**
  - **Stage A (C).** all-on × 50; each of the 9 LOO configurations × 20 (paired with all-on seeds 0–19);
    clean × 5. That is 235 scenarios. Failure-taxonomy precision at n = 50 is about ±8–11 percentage
    points (95 %).
  - **Stage B (A, G),** only after the Stage A pilot shows the runtime is acceptable: all-on × 20, LOO × 10,
    clean × 3. That is 113 scenarios each.

**Analysis.**
- Paired tests on *continuous* metrics: rank-1 error, true-assignment rank, Δcost(truth − rank 1),
  survival stage. Use Wilcoxon signed-rank, or sign tests for binary outcomes. McNemar at n = 20 needs ≥ 6
  discordant pairs, all in one direction (two-sided p = 0.031), so binary per-group comparisons are only
  descriptive.
- All Clopper–Pearson bounds are labelled one- or two-sided. For example, 20/20 → 0.861 one-sided, 0.832
  two-sided.

**Arms on every scenario.**
- V0′ and V1′ (and V0/V1-asdesigned once, on Stage A seeds 0–19).
- **Derived arms at zero compute:**
  - `V0′→V1′ fallback (zero-survivor trigger)`;
  - `V0′→V1′ fallback (absolute-fit trigger)`, HANDOFF's suggested combination and [S §2 hybrid 1];
  - `V0′ + silence-between-events`, a time-mask toggle and cheap rerun [R §4].
- Ladder rungs once Phase 2 starts.

**Metrics** (pre-registered in P0.7 before any run):
- true assignment at rank 1;
- rank-1 error;
- status;
- the precision of "unique";
- confident-wrong rate ("unique" and > 1 km);
- abstention rate;
- survival stage of the true assignment;
- per-event fit z (dv, dd, dt_rx);
- along- and cross-track error, and Δt error vs −clock_error;
- n hypotheses, cap hits, runtime, timeouts;
- harmonic-hop count;
- ellipse coverage and ellipse area, reported as *calibration* metrics, never inside a success test.

**Decision rule for re-ordering Phases 1–3**, fixed now. On all-on C, classify each failure as survival
(the true assignment eliminated, with its stage), discrimination (a wrong assignment ranked 1), or bias (the
right assignment with error > 200 m).
- If ≥ 40 % of failures are survival at CPA, P3.5 goes before Phase 2.
- If ≥ 40 % are discrimination, Phase 2 keeps its place.
- If ≥ 40 % are bias, P1.3 and P3.3 go first.
- Otherwise, the written order stands.

**Compute.** Measured from the skeptic's run.json files: C all-on ≈ 16 s synth + 155 s V1 (mean; 12–502 s)
+ 21 s V0 ≈ 3.2 min; A0 ≈ 7.2 min [R-M4].
- **Before P0.1-i:** Stage A ≈ 235 × 3.2 min ≈ 12.5 h; Stage B ≈ 226 × ≥ 7.2 min ≈ 27 h.
- **After P0.1-i** (enumeration and radius cap) the 400–500 s tails should go, but this is not assumed:
  a 5-scenario pilot (P0.6) re-estimates the compute before Stage A is committed.
- Per-run wall-clock cap: 900 s. A timeout is recorded as its own failure class.

**Disk.** 12 GB free. The driver deletes scenario audio and run directories after each row. It keeps
truth.json, events.parquet and summary rows always, and tracks.parquet (≈ 0.36 MB) only for failing rows.
Each row records git commit, seed and config hash. It stops if free space drops below 3 GB.

### Phase 0: what blocks a meaningful measurement (in this order)

**P0.0 Pin the as-designed variants.**
- Add named variant overlays to config.yaml (`variants: {V0: {...}, V1: {...}}`) and a `--variant` flag.
  run.json records the variant name and the fully resolved config.
- Tag ea8fe0d (e.g. `variants-asdesigned`). Run V0/V1-asdesigned on Stage A seeds 0–19 at that tag.
- Rules: config mechanism only (USER-light, U13). Tests: overlay resolution; run.json records the variant.

**P0.1 Bug fixes that change baseline behaviour.** No methodology rule touched; each restores specified
behaviour. Each has a unit test.

| ID | Fix | Files | Test |
|---|---|---|---|
| a | Newton seed clamped to segment coverage; missing only if the converged t_e is outside it | `score.modeled_ratio` | D-2A regression: 0 false invalids |
| b | Tile pruning needs reach for ≥ `required_matched` events | `cascade.prepare_cascade` | E6: robust gives > 0 survivors |
| h | `manual_inputs` covers non-gpkg `--region`, sidecar time-prior overrides, and a config diff + sha256 against the committed config (`config_overrides`) | `cli.manual_inputs`, run.json | An edited tolerance gives non-empty `config_overrides` |
| i | Best-k branch-and-bound enumeration (ordered by interim \|z\|; a separate partial cap; prune on an empty Δt interval). The CPA KD radius is capped at `cascade.max_event_radius_m` (a priori 12 km = 2 × the largest audible slant; source: audibility physics; PROVISIONAL). Events with σ_d/d > `weak_event_rel_sigma` (a priori 0.5; arbitrary) are scored but don't gate pruning | `cascade.offset_filter`, `cascade.cpa_prefilter`, `cascade.build_event_index` | E2: true assignment kept at N = 2 m = 8, N = 3 m = 4, N = 4 m = 3; C2 and A0 replays < 60 s; offset cell sets otherwise unchanged |

**P0.2 Independent random streams and full truth recording.**
- Per-group, per-aircraft generators `default_rng([seed, group_id, k])`. Every conditional draw (ramp yes/no,
  twin yes/no, c mismatch on/off) is drawn unconditionally, then used or ignored, so toggling a group never
  shifts another group's draws.
- Record every hidden draw in truth.json: ramp parameters, detune, directivity, R, accel, climb, turn, ADS-B
  degradation [P-F15].
- **Consequence:** HANDOFF's single-seed numbers won't reproduce. Re-baseline (this is said, not hidden).
  `--clean` stays bit-identical (an existing test).
- Files: `synth.make_scenario`, `realism.*`. Test: draw hashes are invariant under single-group toggles.
- Effort: about 1.5 days [R §4].

**P0.3 `cmd_locate` refactor into stage functions.**
- Split `cli.cmd_locate` (≈ 380 lines) into `run_locate(cfg, audio, tracks, prior, …) → LocateResult`
  (in-memory: events, cascade summary, ranking, timings) and `write_outputs(result, run_dir)`. The stage
  bodies stay pure in doppler/*; orchestration and I/O stay in cli.py (Conventions).
- The ablation driver calls `run_locate` in-process and shares one event list across arms in memory.
- **Stage I/O seam:**
  - `events --out events.parquet` and `locate --events`;
  - provenance limited to `detected`, with sha256 and a config-hash match;
  - any file that doesn't match its producing run goes into `manual_inputs` automatically;
  - no `manual` enum and no editing affordances [R-M8].
- Runtime claim corrected: sharing detection saves only about 2–10 % of a run [R-M8]. The refactor's value
  is in-process ablation and the seam.
- Tests:
  - `run_locate` + `write_outputs` produce run.json and gpkg identical to the old `cmd_locate` on the `--clean`
    scenarios (test_locate, test_cli_export);
  - events round-trip.
- Effort: about 2 days.

**P0.4 `doppler/evaluate.py` and `cli.py ablate`.**
- Pure metrics (§3.0) and the failure taxonomy.
- The derived arms (§3.0).
- A **provisional absolute-fit status** on every arm, as a diagnostic first [R-M2]:
  - flag `inconsistent` if any matched event's rms exceeds `ambiguity.abs_rms_sigma` × σ_ref, where σ_ref is
    the model-free ridge scatter. Take σ_ref from the first difference of the decimated ridge, corrected for
    lag-1 correlation: σ² = var(Δf) / (2(1 − ρ₁)) [R §3.9];
  - a priori value 3 (arbitrary; 3σ convention; PROVISIONAL);
  - also report the raw rms in Hz. D-4 shows that Hz-level misfit of correct answers overlaps that of wrong
    ones under realism, so this is a status, not a ranking rule.
- A harmonic-hop count.
- The disk-safe driver with the wall-clock cap, free-space guard and per-row provenance.
- Rules: CLAUDE.md layout gains `evaluate.py` and an `ablate` subcommand (USER-light, U13; gates P0.4).
- Tests: fixtures for each metric; the driver deletes scenario directories; derived arms are computed
  correctly from fixture V0/V1 outcomes.

**P0.5 Run the slow test suite once** (HANDOFF: not rerun after the realism round). Fix or record failures
before the baseline.

**P0.6 Pilot, then baseline.**
- Pilot: 5 C scenarios + 1 A scenario on V0′/V1′. Re-estimate compute and check the timeout rate.
- Then Stage A (§3.0). The `path` LOO group is labelled **provisional**: the generator's reflection
  coefficient is real and positive, which is not real ground [R §4, P-F14].
- Include the skeptics' cheapest experiments in the same harness:
  - `corridor_frac` 0/1 with adsb on [P-F2];
  - `rpm_ramp_frac` [0, 0] [P-F1];
  - `twin_detune_frac` [0, 0] and `ground_reflection: false` [P-F14];
  - the fit-error census [S-F2].
- **Done** when Stage A results exist for V0′, V1′, the derived arms and V0/V1-asdesigned (seeds 0–19), with
  the failure taxonomy and the re-ordering rule applied.

**P0.7 A-priori registry of every new knob** [R-M5].
- Before the first measurement that uses a knob, it gets a config entry with:
  - a value;
  - its *source* (physics / standard / literature / measured-in-data / arbitrary);
  - `PROVISIONAL`;
  - a fixed sensitivity grid.
- **Rule written into the plan:** a missed target is reported, never fixed by moving a provisional value.
  Moving a value needs a new 4.5-data or physics source, recorded in the registry. Reserved seeds (P4.1)
  complement this rule; they don't replace it.
- Targets are point targets. For example, 1/40 confident-wrong has a one-sided 95 % upper bound of 0.113, so
  "≤ 2.5 %" can't be demonstrated at n = 40.

| Knob | A-priori value | Source | Sensitivity grid |
|---|---|---|---|
| `cascade.max_event_radius_m` | 12000 | 2 × jet audible slant | 8k, 12k, 20k |
| `cascade.weak_event_rel_sigma` | 0.5 | arbitrary | 0.3, 0.5, 1.0 |
| `cascade.max_partial_per_cell` | 16 | arbitrary | 8, 16, 64 |
| `ambiguity.abs_rms_sigma` | 3 | 3σ convention | 2, 3, 5 |
| `score.outlier_frac` (ε) | 0.05 | arbitrary | 0.01, 0.05, 0.2 |
| `score.adsb_budget` | pos 7 m, speed 0.26 m/s (1 kt / 2), latency 0.5 s | DO-260B NACp/NACv classes; 1 kt quantization; OpenSky latency literature | ×0.5, ×1, ×2 |
| `ambiguity.tie_quantile` | χ²₂ 0.95 | statistics | 0.9, 0.95, 0.99 |
| `ambiguity.gof_quantile` | χ² 0.99 per event | statistics | 0.95, 0.99, 0.999 |
| `score.top_assignments` (K_a), union cap M | 10, 50 cells | arbitrary | 5/10/20, 25/50/100 |
| `score.p_unmatched` (if R3c is built) | 0.2 | arbitrary until P5.3 | 0.05, 0.2, 0.5 |
| `tracks.max_bridge_s` | 30 | 2 × typical OpenSky gap at low level; arbitrary | 15, 30, 60 |
| `tracks.baro_radius_km`, altitude band | 50 km, 300 m bands | meso-scale pressure correlation (literature) | 25/50/100 |
| `silence.source_level_db` (R4) | per class, from the published GA/jet noise certification levels | literature | ±6 dB |
| `silence.null_shifts_s` | ±30, ±45, ±60 s | > 2 × the longest transition time d/v | — |
| `uncertainty.nuisance_sigma` (P1.3) | latency 0.5 s, geo alt 10 m, baro per P3.2, c ±3 m/s, mic ±5 m | as above / physics | ×0.5, ×1, ×2 |

### Phase 1: honest units, status and uncertainty (all arms; diagnostics unless stated)

**P1.1 Calibrated per-event likelihood** (`score.likelihood: calibrated`; Huber stays available).
- σ_e from residuals about the hypothesis' own modelled curve, floored at the ρ₁-corrected model-free
  σ_white (P0.4). The self-referential inflation is capped at 3×.
- Effective N from the residual autocorrelation (`fit.correlation_factor`).
- σ_model² from the ADS-B budget (P0.7), later from P3.1's covariance.
- Bounded per-point loss −ln(ε/W + (1 − ε)·N(z)), where W is the frequency search band in σ units, so both
  terms are densities [R §3.10]. "Unmodellable" costs −ln(ε/W).
- **Fixes:** P-F3, P-F5, P-F6 units.
- **Rules:** step 8 wording → **USER (U1)** for the default; an arm otherwise.
- **Tests:**
  - **Wilks, stated correctly:** on white-noise ridges, 2·(nll(truth) − nll(MLE)) ~ χ²₃ for (E, N, Δt) with
    f0 profiled, *for interior optima only* (excluding the Δt prior edge and the cell bound). 200 Monte Carlo
    trials, KS p > 0.01 [R §3.8].
  - Correlated ρ₁ = 0.6: the calibrated scale is within 20 % of the empirical one.
- **Measure:** 2Δnll(truth − MLE) distribution on Stage A clean and all-on, paired with V1′.
- **Cost:** 1.5 days.

**P1.2 Assignment-level ranking and per-assignment refinement.**
- Group hypotheses by icao24 tuple. Refine the top K_a assignments continuously over the union of their top-M
  surviving cells.
- Outputs:
  - an `assignments` layer;
  - the per-event score scatter in viewer.html (imports L598–613);
  - the 40 m fine cost raster for the top 2–3 assignments (L641–648), *after* the core items
    (nice-to-have).
- **Rules:** a new output layer is fine. Making the assignment optimum the headline estimate → **USER
  (U9)**.
- **Tests:** a valley straddling two cells reaches the true optimum; grouping is order-invariant.
- **Measure:** true-assignment rank vs true-cell rank, paired.
- **Cost:** 1 day.

**P1.3 Systematic-aware ellipse and degeneracy flag (report-only).**
- Hessian in (E, N, Δt) plus G·Σ_nuis·Gᵀ (P0.7 σs), the along-track lever per event pair, and a
  `prior_bounded` flag.
- No per-track parameter is fitted, so locked scoring is untouched.
- **Validation honesty** [R-M7]:
  - the nuisance σs equal the realism draws, so synthetic coverage is self-consistent by construction and
    proves only that the propagation is implemented correctly;
  - real coverage can be validated only on 4.5 recordings / P4.3 replay;
  - coverage is reported **per group** with an explicit line "not covered: source (RPM) changes", which is
    the largest bias.
- **Tests:** a Monte Carlo of the *production* estimator on the D-2 geometries checks that the reported
  covariance matches the empirical scatter within 25 %. Not pinned to D-2D's scratch numbers [R §3.3].
- **Cost:** 1 day.

**P1.4 Ambiguity v2.**
- Replaces `score.ambiguity`.
- Statuses {unique, ambiguous, inconsistent, locus, none}. The full ranking is always written.
- **Tie set:** Δnll ≤ `tie_quantile`, across unmatched counts once R3 exists.
- **Assignment gap** [C-#12].
- **`inconsistent`:** the absolute-fit test (P0.4, re-expressed with P1.1 σ), or `prior_bounded`.
- **`locus`:** a single event, with both mirror candidates and the along-track spread v·clock_tol.
- **Held-out-event check for N ≥ 3:** low priority, because N ≥ 3 is rare in 60 s clips [R §4].
- **Rules:** the ambiguity design was delegated by the user (SETTLED, S7); thresholds per P0.7.
- **Measure:** the precision of "unique", abstention, and confident-wrong, paired with V1′'s status, on
  Stage A and on E1 `noadsb2` (all 9 runs at K ≥ 60 plus intact controls).
- **Cost:** 1 day.

**P1.5 Clock-prior misspecification diagnostic** (new [R §4, S open Q5]).
- Run the offset filter once with a wide prior (±300 s) on cells within the CPA survivors, and report the
  histogram of feasible Δt. If its mass lies outside the configured prior, set status note
  `clock_prior_suspect`.
- Diagnostic only; never changes the run.
- **Test:** a scenario with clock error 30 s and prior ±10 s raises the flag.
- **Cost:** 0.5 day.

**P1.6 Reduced user sensitivity sweep** (HANDOFF step 3, brought forward [R-M10]).
- Right after Phase 1, on Stage A all-on seeds 0–19, paired, V0′ and V1′ (+ calibrated):
  - SNR offset {−6, 0, +6} dB;
  - `n_sigma` {2, 3, 4};
  - DEM bias {−30, 0, +30} m, one factor at a time.
- This needs only a uniform DEM offset, so no download.
- The full sweep stays in P4.4. Timing: U15.

**Phase 1 done** when:
- P1.1–P1.5 are merged with tests;
- Stage A is re-reported with assignment rank, statuses, precision of "unique", per-group ellipse coverage,
  and the derived fallback arms;
- the reduced sweep is reported.

### Phase 2: the ladder (V2 is its best rung)

Every rung is one config flag on top of the previous rung. Each is evaluated on the shared Stage A seeds,
paired against the previous rung. A rung is kept only if the precision of "unique" doesn't drop, and either
the confident-wrong rate or the true-assignment rate improves (sign test / Wilcoxon, reported with effect
sizes). V2 = the highest kept rung. V0′ and V1′ are unchanged throughout.

| Rung | Adds | Fixes |
|---|---|---|
| R1 | P1.1 calibrated likelihood as the score | P-F3, P-F5 |
| R2 | **Per-hypothesis silence** (explainers = assigned tracks, at the hypothesis' Δt); **undetermined passes** (slow < `min_aircraft_speed_mps`; gaps longer than `max_bridge_s`); **minimal linear gap bridging** in ECEF up to `max_bridge_s` (a subset of P3.1, so the undetermined-gap mechanism is not throwaway [R §4]); **dedupe tolerance from the fit covariance** ⊕ a sys term equal to the current constant, adopted only if truth-event recall doesn't drop (P3.6 moved here [R-M6]) | S-F4, S-F5, P-F6b, S-F12 |
| R3 | Unmatched-event ranking. **Measure first:** for each option, the fraction of true events left unmatched at the true assignment on intact realistic runs, and confident-wrong on E1 `noadsb2`. Pre-registered acceptance: true events stay matched in ≥ 90 % (value fixed now, P0.7). Options: **R3a** GOF-gated lexicographic (a matched event failing the per-event `gof_quantile` counts as unmatched; no new null); **R3b** evidence with a fixed −ln p_u plus a **track-choice prior** −ln(1/K_e,cell), with K the class-compatible candidate passes for that event at that cell, as a look-elsewhere term [R-M1.4]; only after R1 shows calibrated Δnll at true events is χ²-like. The free straight-fit null of revision 1 is **dropped** (D-5). | S-F1, P-F7 |
| R4a | Audibility from the measured local floor at each unexplained pass's received time ([C-#10]) | S-F2 silence kills, S-F7 inertness |
| R4b | Forced detection along the unexplained pass's modelled curve (comb excess vs floor, time-shift null), only if R4a leaves silence-driven failures | the same, stronger |

**Rung items, once each.**

- **R2: per-hypothesis silence.**
  - Files: `cascade.silence_filter`, `cascade.run_tile`, `score.rank_cells`; config
    `silence_explain: hypothesis` (legacy `cpa` kept).
  - Tests: E3 gives one unexplained pass per hypothesis; E4 gives 0 at the robust true cell with bridging;
    E5 doesn't count a slow helicopter.
  - Seam: the culprit list per hypothesis is exported (future "exclude a track" is data).
  - Rules: SETTLED as an option and in the ladder (S8). Flipping the V0′/V1′ defaults → **USER (U12)**.
- **R3.**
  - Files: `score.CellScore.rank_key`, `score.rank_cells`, `evaluate.py`.
  - Rules: changing ranking for a default → **USER (U8)**.
  - Locked timing: no per-event fitted t0 enters anything.
  - "Reject event" as a future HITL decision means *removing the event from the event file* (P0.3 seam),
    not setting a prior [R §3.11].
- **R4a/R4b.**
  - Files: new pure `score.comb_excess`, `cascade.forced_detection_silence`; ISO 9613 moved to physics.py.
    Spectrogram windows per pass, not cached.
  - Rules: step 6 audibility → **USER (U5)** for any default.
  - **Self-consistency guard** [R-M7]: before R4 is evaluated, the *generator* gets level and propagation
    models the pipeline does not use (ground-effect excess attenuation, complex ground reflection with phase
    (Delany–Bazley impedance), directivity per type). The pipeline keeps the simple class-level prior. Even
    so, synthetic R4 results are labelled "synthetic; real validation at 4.5".
  - Tests:
    - an inaudible −23 dB near pass doesn't reject;
    - a loud pass withheld from the events gives "energy found → missed event";
    - masked by a car gives weak evidence;
    - a quiet clip with a +10 dB predicted pass rejects;
    - V0′ decoy tests still pass.
  - Cost: R4a 1 day; R4b 3 days.

**Phase 2 done** when each rung has a paired comparison with the previous one on Stage A (and Stage B if
run), and V2 is named.

### Phase 3: bias side (track, source, tolerances) and hygiene

- **P3.1 Track smoother with covariance, in ECEF** [R-M11].
  - Start with a velocity-fused smoothing spline per axis in ECEF (lower risk [R §4]). Constant velocity is
    frame-agnostic; any turn model uses the local vertical computed from the ECEF position.
  - Per-row velocity, MLAT down-weighting, bridging with covariance; σ_model per sample to P1.1 and P1.3.
  - Files: `tracks.interpolate_track` → `tracks.smooth_track`, `TrackSet` σ arrays.
  - Tests: 7 m noise gives < 0.08 Hz RMS near CPA (from 0.16 [P-F8]); MLAT spikes are removed; a 25 s gap
    over CPA is bridged with σ.
  - Rules: none (ECEF kept; S13).
  - Measure: adsb LOO, paired.
  - Cost: 2 days.
- **P3.2 Baro correction from dual-reporting aircraft** (radius and bands per P0.7).
  - Files: `tracks.tracks_from_frame`, `tracks.baro_correction`.
  - Test: +150 m QNH offset corrected to within 20 m.
  - Rules: the CLAUDE.md "pressure correction" (S11).
  - Needs realism `baro_offset_mode: shared_qnh` for evaluation.
- **P3.3 RPM.**
  - (a) Swing-consistency z and a residual step detector: a diagnostic on all arms.
  - (b) Linear f0 drift in scoring as an arm (**USER U2** for any default).
  - Gate: on the RPM LOO subgroup, paired, the clean assignment gap (Δnll best vs second assignment) must not
    shrink by > 10 % [R §3.2].
- **P3.4 Comb-power second score and ridge coverage** of the modelled curve (diagnostic; S4).
  - Tests P-F9 with a turn-rate subgroup, and [C-#5] via hop counts.
- **P3.5 Fit-bias census → tolerance structure.**
  - Structure:
    - t_rx stays hard;
    - v = covariance ⊕ physics wind (w_max·v/c) ⊕ **per-pass kinematic spread from ADS-B ground speed
      within ±2 d/v of CPA** (measured in the data, [C-#8]) ⊕ `fit_model_sys_mps`;
    - d becomes soft or wide.
  - `fit_model_sys_*` values come only from 4.5 data, never synthetic quantiles (S10). Until then the total
    allowance equals today's.
  - The split and the default → **USER (U4)**.
  - Measure: survival per stage and survivor counts, paired; US pruning via P5.2.
- **Hygiene group H** (needed before milestone 7, moved out of Phase 0 [R-M3]):
  - **H-c** per-row velocity fallback (absorbed by P3.1 if it lands first);
  - **H-d** count NaN-DEM cells as `grid_nodata` and check undeclared nodata values;
  - **H-e** dataclass defaults (unknown 6000 → 3000);
  - **H-f** dropped. Per-cell silence-penalty normalisation is superseded by R2/P1.1.
  - **H-g** refinement cap re-specified: refined and unrefined cells in explicit separate tiers, with
    cap-hit counts reported; no lower-bound claim [R §2].

**Milestone 6** (exports + QGIS MCP) goes after Phase 3, when the layers are stable: assignments, ellipse,
fine raster, statuses. Styles and the template are updated then. This follows the CLAUDE.md milestone
order (S15).

### Phase 4: validation that breaks the circularity
- **P4.1 Protocol.**
  - Freeze config.yaml (sha256 in a manifest).
  - Reserved seeds 10000–10049, never inspected during development.
  - The P0.7 registry is frozen.
  - Metrics and success definition (U10) as pre-registered. n = 50 per configuration.
- **P4.2 Generator additions** (scenario settings, not pipeline thresholds):
  1. traffic-model placement independent of pipeline thresholds, including traffic inside 3 km, with
     silence truth from synthesised SNR;
  2. traffic-model decoys;
  3. unprotected drops {0, 0.3, 0.5} including events, plus forced gaps over CPA;
  4. fast ground sources (highway 30–40 m/s, trains 30–80 m/s, motorcycles, drones);
  5. airport pattern traffic and a same-line approach stream;
  6. a real DEM with bias and noise and a nodata patch: **a 3DEP download for the Boulder region (USER,
     U14)**;
  7. sub-0 dB events;
  8. shared-QNH baro;
  9. the R4 guard models (ground effect, complex reflection).

  Lossy codec and VAD wait for milestone 8.
- **P4.3 Real-traffic replay** with injected events. **OpenSky hour download (USER, U14).**
- **P4.4 The full user sweep** (SNR, n_sigma, each sys ×{0.5, 1, 2}, DEM bias and noise, slope) on P4.2
  scenarios, all arms. Reported, not tuned.
- **Done** when the frozen, reserved-seed table exists for V0′, V1′, V2 and the derived fallback arms.

### Phase 5: scale and real data (milestones 4.5, 7)
- **P5.1** Ragged / time-chunked TrackSet, batched centre scoring, top-K_a refinement. Tiered anchors as a
  pruning test only.
- **P5.2** Synthetic CONUS test: one real OpenSky hour + injected events. Done = < 1 h and < 8 GB RAM for a
  1-minute clip.
- **Storage budget** [R §4]:
  - A 30 m CONUS DEM is ≈ 9×10⁹ pixels, ≈ 18 GB uncompressed and ≈ 6–10 GB compressed. That exceeds the
    12 GB free. Fetch DEM tiles **lazily, only for tiles active after pruning** (tiles are already skipped
    before the DEM read), with a per-run cap.
  - A CONUS OpenSky hour, cropped to the needed columns, is ≈ 50–300 MB.
  - A dense TrackSet for 1 h and 5,000 tracks is ≈ 3.6 GB RAM, so P5.1 is required first.
  - Each download is approved by the user (U14).
- **P5.3 Coverage measurement**: UAT-capable feed vs OpenSky near KBJC/KBDU. *Started alongside Phase 1*,
  because p_u, drop rates and source levels hinge on it (U16).
- **P5.4** Milestone 4.5 field recordings set the provisional values (audibility levels, σ_L,
  `min_aircraft_speed_mps`, `fit_model_sys_*`, ε, swing distribution, p_u). Also check OpenSky geoaltitude
  HAE vs MSL against runway elevation on short final [P open Q2]. Request the recordings now (U16).
- Then milestone 7: regional, then the US-wide benchmark, all arms, pre-registered metrics.

### Deferred (with evidence)
- **Global c / wind and mic-height search:** single-effect 4–43 m and 4–17 m [P-F10]. Asked as U3, with
  "defer until the paired c/wind and mic-height LOO subgroups show an effect" recommended.
- **Fitting a per-track timing nuisance:** not proposed. Report-only (P1.3; S2).
- **Clock skew** [P-F13]: NEEDS-TEST after Phase 3 (resample scenario F by 1 + 5e-5).
- **Harmonic-agnostic residual** [C-#5]: after the hop counts.
- **Window / relative modes** [S-F13]: after P5.1.

### Dependency order
P0.0 → P0.1 (a, b, h, i) → P0.2 → P0.3 → P0.4 → P0.5 → P0.7 → P0.6 (pilot, Stage A) → P1.1 → {P1.2, P1.3,
P1.5} → P1.4 → P1.6 → R1 → R2 → R3 (measure, then build) → R4a → (R4b) → {P3.1 → P3.2}, P3.3, P3.4 → P3.5
→ H → M6 → P4 → P5.1 → P5.2 → milestone 7.

P5.3 and the 4.5 request run in parallel from Phase 1.

Rough effort: Phase 0 ≈ 8 days + 13–40 h background compute; Phase 1 ≈ 5 days; Phase 2 ≈ 7 days; Phase 3
≈ 7 days. Phases 4–5 are milestones.

---

## 4. Decisions (final)

### 4a. SETTLED (evidence and existing CLAUDE.md constraints decide them)

| # | Decision | Answer and justification |
|---|---|---|
| S1 | Order of work | Bug fixes that change baseline behaviour (P0.1 a, b, h, i) and the harness come before the ablation. HANDOFF puts the ablation first; P-F4 changes ranks, S-F3 empties V1, and S-F6 / the CPA radius produce 400–500 s runs. No rule changes. |
| S2 | Per-track ADS-B timing nuisance | Report-only, through P1.3 marginalisation. It keeps the locked-scoring rule as written; only a proposal to *fit* it would need the user [R §2]. |
| S3 | Adding comparison arms (ladder, derived fallback arms, between-events toggle) | Yes. Arms change no default. HANDOFF requires side-by-side comparison, and its strict-first fallback is evaluated as a derived arm at zero compute. |
| S4 | Comb-power score | Diagnostic only now (Operating modes allow read-only diagnostics). Using it for ranking would be a later USER question. |
| S5 | DEM nodata | Count it (H-d). Silently losing cells contradicts "always report cell counts". *Filling* is U6. |
| S6 | Realism additions and their order | P4.2 list. They are scenario settings, not thresholds. Downloads are excluded (U14). |
| S7 | Ambiguity design | P1.4. The user delegated it ("up to you", HANDOFF). Thresholds are a priori (P0.7). |
| S8 | Per-hypothesis silence | Implemented as an option and used in the ladder (R2). Correctness: one aircraft per event. Changing V0′/V1′ defaults is U12. |
| S9 | Undetermined passes (slow, beyond-bridge gaps) | Option, in R2. It is within the Coverage-gaps pitfall ("strictness configurable, report counts"). |
| S10 | `fit_model_sys_*` from synthetic quantiles | No. CLAUDE.md forbids tuning provisional thresholds on synthetic data. Values come from 4.5 data. |
| S11 | Baro correction from dual-reporting aircraft | Yes. It is the pressure correction CLAUDE.md asks for, measured from the data. |
| S12 | Dedupe tolerance | Covariance ⊕ a sys term equal to today's constant, adopted in R2 only if truth-event recall doesn't drop (paired). Under realism the covariance is overconfident, so a covariance-only tolerance could under-merge [R §2]. |
| S13 | Track smoother frame | ECEF (velocity-fused spline / CV), not ENU, so the CLAUDE.md geometry rule is unchanged [R-M11]. |
| S14 | Protection against tuning on synthetic data | The P0.7 a-priori registry with sources and fixed sensitivity grids, plus "missed targets are reported, never fixed by moving provisional values", plus reserved seeds. |
| S15 | Milestone 6 placement | After Phase 3, before Phase 4/5, following the CLAUDE.md milestone order (6 before 7). |
| S16 | Event-file provenance | `detected` + hash only. Anything else is a manual input. No `manual` enum or editing affordances (CLAUDE.md: no HITL tooling yet). |

### 4b. USER (standalone questions; each with options, my recommendation, and what would change it)

**U1. May the score's error scale include a model-error term and become a calibrated log-likelihood
(changing step 8's "pooled mean Huber loss in units of each event's ridge scatter")?**
- Options: (a) keep Huber; (b) calibrated as a comparison arm only; (c) calibrated as the default.
- **Recommend (b) now, then (c)** if R1 meets its paired acceptance test and the P1.1 Wilks test passes on
  clean data. It is needed for meaningful statuses (P1.4) and any evidence ranking (R3b).
- Would change: R1 losing true-assignment rate in the paired test, or the Wilks test failing.

**U2. May scoring add one linear f0-drift parameter per event (a new step 8 free parameter)?**
- Options: (a) no; (b) as an arm only; (c) default.
- **Recommend (b).** D-2C halves ramp bias in 2 of 3 geometries with an unchanged CRLB; residual reduced χ²
  stays at about 2.9.
- Would change: 4.5 swing data showing real RPM changes are rare (→ a), or the RPM LOO subgroup showing a
  large paired gain without a > 10 % loss of the assignment gap (→ c).

**U3. Should the speed-of-sound search and mic-height search from HANDOFF next step 2 be built now, or
deferred?**
- Options: (a) build both now; (b) defer both until the paired c/wind and mic-height LOO subgroups are
  measured; (c) drop them.
- **Recommend (b).** Single-effect biases are 4–43 m (c) and 4–17 m (mic height) against 240–700 m for
  ramps. But c can flip the winning side of a degenerate valley [P §2], so measure before dropping.
- Would change: the paired subgroup showing a median rank-1 error change > 50 m (→ a).

**U4. Should `wind_sys_mps: 15` be split into a physics wind term (≈ 2 m/s), a per-pass ADS-B kinematic
term, and a named `fit_model_sys_mps`?**
- Options: (a) leave as is until 4.5; (b) split now, keeping the total allowance equal to today's; (c) split
  and tighten now.
- **Recommend (b).** Tightening now would kill true cells [S-F10]. Splitting makes the allowance honest and
  lets the kinematic term come from data.
- Would change: the census showing v errors < 5 m/s at the 99th percentile after P3.3, together with 4.5
  data (→ c).

**U5. May audibility in the silence step come from the audio (measured noise floor, then forced detection
along the pass's modelled curve) instead of a slant threshold (changing step 6)?**
- Options: (a) keep slant thresholds; (b) R4a/R4b as arms only; (c) default if the ladder keeps them.
- **Recommend (b), then (c)** after 4.5 confirms the source-level prior. Synthetic results are
  self-consistent by construction even with the generator guard.
- Would change: R4 failing its paired rung test, or a 4.5 level scatter ≫ 6 dB (→ a).

**U6. How should cells with no DEM value (e.g. over water) be filled?**
- Options: (a) drop and count only; (b) a fallback global DEM for those tiles (a download); (c) water at
  0 m orthometric.
- **Recommend (b), with (a) until the download is approved.** (c) is wrong for inland lakes (the Great
  Lakes are ≈ 176 m).
- Would change: a coastal-only benchmark (→ c is acceptable there).

**U7. Should CLAUDE.md's single-event pitfall be reworded to: "One straight flyover constrains the mic to two
mirror loci of length ≈ v × clock uncertainty; report status `locus` with both candidates; 'unique' needs
≥ 2 events from different tracks or a track whose turn breaks the mirror"?**
- Options: reword / keep.
- **Recommend reword.** With a precise clock one event gives a small region [T-F2]; the current wording
  says ranking among locus cells is "not meaningful".
- Would change: none expected.

**U8. How should hypotheses that leave events unmatched be ranked, and which arm becomes the default?**
- Options:
  - (a) lexicographic (V1, current unapproved default);
  - (b) GOF-gated lexicographic (R3a);
  - (c) evidence with a fixed −ln p_u plus a track-choice prior (R3b);
  - (d) strict-first with robust fallback on an absolute-fit trigger (a derived arm).
- **Recommend:** keep (a) as the default for now. Decide between (b), (c) and (d) on R3's measure-first
  results, choosing the option that keeps ≥ 90 % of true events matched on intact realistic runs and lowers
  confident-wrong the most. D-5 shows the obvious free-fit evidence rule fails; D-1/D-4 show fit quality
  alone separates right from wrong only on clean ridges.
- Would change: P5.3 showing p_u ≈ 0 (→ strict-first becomes attractive), or ≈ 0.5 (→ (c)).

**U9. Should the headline location estimate be the refined optimum of the best track assignment (over its
cells) rather than the refined point of the best cell (step 8's wording)?**
- Options: assignment optimum / cell refined point.
- **Recommend the assignment optimum**, keeping the cell ranking and score.tif. It removes the cell-crowding
  artefact [P §2.5].
- Would change: the paired comparison showing a larger median error for the assignment optimum.

**U10. What is "success" in validation, and how are abstentions scored?**
- Options:
  - (a) CLAUDE.md as written: the true location ranks 1st;
  - (b) the true assignment at rank 1 *and* error < D, with D fixed now (150 m or 200 m), the status not
    "unique-and-wrong", abstentions counted separately;
  - (c) a single number with abstentions counted as failures.
- **Recommend (b) with D = 200 m** (the grid spacing, chosen before any result). Ellipse coverage and area
  are reported separately as calibration, never inside the success test, so wide ellipses cannot buy
  success [R-M9].
- Would change: a user preference for one number (→ c).

**U11. When the result is `inconsistent` or `ambiguous`, should the pipeline still write the ranked location,
or withhold it?**
- Options: always write it, with the status shown prominently / withhold.
- **Recommend always write.** Withholding hides evidence from a later analyst and from validation.
- Would change: an operational requirement for a no-answer output.

**U12. May the corrected behaviours (per-hypothesis silence; undetermined passes) become the defaults of the
two kept variants, so "V0/V1" mean V0′/V1′, with the as-designed versions pinned (config overlay + git tag)
and run once on the baseline seeds?**
- Options: (a) yes; (b) keep V0/V1 exactly as designed and add the corrections only in the ladder.
- **Recommend (b)** until R2's paired result is in, then (a) if R2 helps both.
- Would change: R2 not improving V0′/V1′.

**U13. May the repo layout and config gain `doppler/evaluate.py`, a `cli.py ablate` subcommand, a
`--variant` flag with named variant overlays in config.yaml, and the `run_locate` / `write_outputs`
split of `cmd_locate`?**
- Options: yes / a different structure.
- **Recommend yes.** All are needed for in-process, paired, disk-safe ablation, and they keep file I/O in
  cli.py.
- Would change: a preference to keep ablation tooling out of the repo (→ scratch scripts, losing tests).

**U14. Do you approve these downloads, each when its phase starts?**
- (i) a 3DEP 1/3″ DEM for the Boulder search region (≈ 50–150 MB);
- (ii) a fallback global DEM tile set for any nodata tiles (≈ 25 MB per 1° tile);
- (iii) OpenSky state vectors: 1 h for the Boulder region (≈ 10–50 MB cropped) and 1 h CONUS (≈ 50–300 MB
  cropped);
- (iv) a one-day sample from a UAT-capable feed near KBJC/KBDU (size depends on the source).
- **Recommend yes to (i) and (iii)-regional now; the rest when Phase 5 starts.** 12 GB is free.

**U15. Is it acceptable that HANDOFF's full SNR / tolerance / DEM sweep runs in Phase 4, with a reduced
paired version right after Phase 1?**
- Options: (a) reduced after Phase 1 and full in Phase 4; (b) full sweep right after Phase 1; (c) full sweep
  right after the baseline.
- **Recommend (a).** The full sweep on scenarios that are still circular would have to be redone after P4.2.
- Would change: needing the sensitivity numbers sooner for an external reason (→ b).

**U16. When can you provide the milestone-4.5 known-location field recordings, and may the UAT-coverage
check (P5.3) start alongside Phase 1?**
- **Recommend:** start P5.3 now (it needs U14 (iv)). Plan the recordings for the end of Phase 2. p_u, the
  source levels, `fit_model_sys_*` and the RPM prevalence all wait on them.
- Would change: if no recordings are possible, every provisional value stays provisional, and milestone 7
  results must say so.

**U17. Is about 13–40 h of background compute (Stage A, then optional Stage B) acceptable for the
baseline?**
- Options: yes / Stage A only (≈ 13 h before the P0.1-i speed-up; re-estimated by the pilot) / a smaller
  design.
- **Recommend Stage A now, and Stage B only if the pilot shows ≤ 4 min per scenario.**

---

## 5. Risks and what would invalidate the plan
1. **The baseline reorders Phases 1–3** via the fixed rule in §3.0. Robust failures in S-1a were mixed: 1
   survival (C4), 2 discrimination (C1, C6), 2 bias (C0, C2); strict failed on survival in 8/8.
2. **Calibration may be impossible under structured error** (ramps, notches). Then R1, R3b and the
   calibrated statuses fail their tests. Fall back to the model-free absolute status (P0.4) and the
   distribution-free comb-score null.
3. **Fit quality does not separate right from wrong under realism** (D-4: wrong C1 fits better than correct
   C5). If that holds on real data, only better modelling (P3), silence (R2/R4) and more events can fix
   discrimination. Statuses can only lower confidence.
4. **ADS-B coverage of GA props.** If p_u ≈ 0.5, two-event clips will often be `locus`, which is honest but
   lowers the benchmark's success rate.
5. **Physical limits.** Corridor geometry with a ±10 s clock gives long ellipses whatever the scorer does
   (D-2B). The success test (U10) must not demand < D there blindly. Report a geometry class with each
   result.
6. **Self-consistent synthetic validation of the new items** (P1.3 σs, R4 levels). The generator guard and
   "real validation at 4.5" labels mitigate it, but synthetic wins for these items are weak evidence.
7. **Tuning creep.** Mitigated by P0.7, the frozen registry and reserved seeds. The residual risk is
   choosing *which* rung to keep after seeing the results. That is why the ladder acceptance rule is fixed
   now.
8. **Compute and disk:** 13–40 h; 12 GB free. Mitigated by the driver's guard and the pilot.
9. **US scale:** if P5.2 leaves > 10⁵ assignments, pre-ranking or anchors are needed, each with its own
   survival measurement.
10. **Core method.** Real cross-aircraft ADS-B timestamp errors routinely > 2 s would degrade crossing
    geometry to about 150 m and make corridors prior-bounded. Check per-track Δt residuals in 4.5.

---

## 6. Revision log (reports/plan_review.md)

| Review point | Resolution |
|---|---|
| **M1** D-1 doesn't support P2.3 | **Accepted.** I re-read D-1: the partial winners are 7.1–9.5 km away and the label was hard-coded; D-1 is now described as supporting abstention, not recovery. I added D-5 (arithmetic on S-1a + [P-F3] σ): the revision-1 free-fit null leaves true events unmatched (RSS ratio ≈ 256 in C0), so that null is **dropped**. R3 is now measure-first with a pre-registered ≥ 90 % true-match criterion, options R3a/R3b, and the track-choice (look-elsewhere) prior added. |
| **M2** absolute-fit check earlier | **Partly accepted.** Moved into P0.4 as a status on all arms, with the ρ₁-corrected model-free σ. **Rejected as sufficient:** D-4 on the realistic sweep shows it catches 3 of 5 confident-wrong answers but misses C1 (wrong rank 1 fits at 0.63/0.30 Hz, better than correct C5 at 1.46 Hz) and flags the right assignment in C0. It is a status, not a ranking rule, and discrimination work stays. |
| **M3** Phase 0 composition | **Accepted.** P2.5 → P0.1-i, adding the CPA radius cap: A0's 157–162 s is in `cascade_cpa_s` (CPA prefilter), not enumeration, which I checked in `run_tile`. Added the wall-clock cap, the `cmd_locate` refactor (P0.3) and the slow suite (P0.5). Velocity fallback, nodata, defaults and refinement cap moved to group H; per-cell penalty normalisation dropped as throwaway. Velocity fallback confirmed unexercised (`realism.degrade_adsb` interpolates velocity for every fix). |
| **M4** compute and power | **Accepted.** Recomputed from run.json (C robust mean 155 s, strict 21 s). Paired design, continuous metrics, the reviewer's seed allocation, one/two-sided labels, a quantitative re-ordering rule, and a pilot. One correction: at n = 50 the failure-rate precision is ±8–11 pp, not ±7. |
| **M5** knobs exposed to synthetic tuning | **Accepted.** P0.7 registry with a-priori values and sources, fixed grids, and a "report, don't move" rule; targets labelled as point targets (1/40 → upper bound 0.113). |
| **M6** V2 bundle | **Accepted.** Ladder R1–R4b, each paired against the previous rung; P3.5 removed from V2; dedupe moved into R2; R4 staged a → b. |
| **M7** circularity overstated | **Accepted.** P1.3 coverage reported per group with "not covered: RPM", and validated for real only at 4.5; R4 generator guard with models the pipeline doesn't use. The V0 verdict is split: mechanism confirmed, frequency NEEDS-TEST. |
| **M8** P0.3 runtime claim and HITL edge | **Accepted.** The 2–10 % saving is stated; provenance limited to `detected` + hash; no `manual` enum. |
| **M9** success rewards wide ellipses | **Accepted.** U10 recommends a fixed D = 200 m; ellipse metrics are kept out of the success test. |
| **M10** sweep delayed; fallback untested | **Accepted.** Derived fallback arms (zero-survivor and absolute-fit triggers) at zero compute in P0.4; reduced sweep P1.6 after Phase 1; timing asked as U15. |
| **M11** ENU filter; changing V0/V1 | **Accepted.** Smoother in ECEF (S13); as-designed variants pinned (P0.0); V0′/V1′ naming; flipping defaults is U12. |
| Re-class 2a c/wind, mic height | **Accepted** → U3. |
| Re-class 16 per-hypothesis silence | **Accepted** → S8 (option and ladder) + U12 (defaults). |
| Re-class P3.1 ENU | **Accepted** → redesigned in ECEF (S13). |
| Re-class 20 dedupe | **Accepted** (NEEDS-TEST, then adopt): S12 is gated on recall, with a sys term; D-3 noted as conditional. |
| Re-class 18 fit_model_sys | **Accepted** → S10. |
| Re-class 2c timing nuisance | **Accepted** → S2. |
| Re-class 14 success | **Accepted**: the recommendation changed (U10). |
| Re-class 9 realism / downloads | **Accepted**: downloads → U14. |
| Re-class P0.1-g | **Accepted**: re-specified as explicit tiers (H-g). |
| Re-class 21 layout | **Accepted**: U13, which gates P0.3/P0.4. |
| §3 evidence items 8–11 (Wilks, σ_white, density units, reject-event seam) | **All accepted** and fixed in P1.1, P0.4 and R3. |
| §3.3 D-2D regression test | **Accepted**: replaced by a production-estimator Monte Carlo. |
| Should-fix items: between-events silence, [C-#8], P2.2 throwaway, P5.3 timing, clock diagnostic, geoaltitude, reflection model, US storage, milestone 6, S-F13/S-F14, config keys, row provenance, P0.2 size, held-out N ≥ 3 | **All accepted**, placed respectively in P0.4 / §3.0 arms, P3.5, R2 bridging, P5.3 + U16, P1.5, P5.4, P0.6 + P4.2, P5 storage, S15, §2 rows, the P0.7 table, §3.0 disk, P0.2, P1.4. |
| Nice-to-have: fine raster later, spline first, staged R4, variant mechanism | **Accepted** (P1.2, P3.1, R4a/b, P0.0). |
| Anything rejected | (1) M2's implication that the absolute check could stand in for ranking work (D-4). (2) The A0 runtime attribution (CPA prefilter, not enumeration); the remedy, P0.1-i with a radius cap, covers both. (3) "±7 pp" precision, corrected to ±8–11 pp. Nothing else. |

---

## 7. Decision review (main session, after revision 2)

The user asked the main session to review the 17 USER decisions, accept them where they fit the
existing design, and flag anything that could cause major problems. Status of each:

**Accepted as recommended:** U1 (calibrated likelihood as an arm; default only if it passes), U2 (drift
arm only), U3 (defer the c and mic-height search until measured), U4 (split `wind_sys_mps`, total kept at
15 m/s), U5 (silence from audio as arms), U6 (fallback DEM; drop and count until then), U7 (reword the
single-event pitfall), U8 (keep lexicographic until R3 is measured), U9 (assignment optimum as the
headline, cell ranking and score.tif kept), U11 (always write the ranking with a status), U12 (V0/V1
behaviours unchanged until R2), U13 (evaluate.py, `cli.py ablate`, variant overlays, cmd_locate split;
the CLAUDE.md layout is updated when they land), U15 (reduced sweep after Phase 1, full sweep in
Phase 4), U17 (Stage A now; Stage B only if the pilot shows ≤ 4 min per scenario).

**Flagged for the user:**
- **F1. P0.1-i is not purely a bug fix.** The best-k ordering and the radius cap are fixes. But
  "events with σ_d/d > `weak_event_rel_sigma` are scored but don't gate pruning" changes methodology
  steps 4–5 and the strict variant's "every event matched" semantics, inside the baseline (V0′/V1′).
- **F2. U10 rewrites the CLAUDE.md Validation success criterion** ("the true location ranks 1st").
- **F3. U14 / U16 need inputs only the user can provide:**
  - OpenSky historical access needs the user's account; pyopensky would force the CLAUDE.md
    pyarrow-for-fastparquet switch, a dependency change;
  - the UAT-capable feed's source and its terms are unspecified;
  - only the user knows when field recordings are possible.

**Notes, no decision needed:**
- Until P1.4 lands, the current default's "unique" label is not trustworthy (S-F1 confident-wrong).
  Don't use it for any real-data claim.
- CLAUDE.md doc fixes to ride along with U7:
  - the Wind pitfall's travel-time figure is about 3× too low (≈ 0.085 s/km per 10 m/s, P-F10);
  - `environment.temperature_search_c` is in config but no code reads it.

**User answers (session 4):**
- **F1:** the weak-event exemption in P0.1-i becomes a config option, on in the robust variant (V1′) and
  off in the strict variant (V0′), so strict keeps "every event matched". The best-k enumeration and the
  radius cap apply to both.
- **F3 / U14 / U16:** OpenSky historical access comes now, from the user's own account. The UAT-capable
  feed and the field recordings come later, so Phase 4–5 stay as written. Adding pyopensky triggers the
  CLAUDE.md pyarrow switch; explain that dependency before adding it.
- **F2 / U10: tiered and location-based.** Success means the headline estimate (the best assignment's
  refined optimum, U9) is within 200 m of the truth and is not confident-wrong. The 200 m is a fixed
  validation constant in config and does not follow `grid.spacing_m`. On synthetic data the true
  assignment must also be at rank 1. On real data, assignment correctness is reported when identifiable
  but is not required. Each run is classed correct / abstained (ambiguous, inconsistent, locus) / wrong /
  confident-wrong, and the abstention and confident-wrong rates are reported separately. "True cell ranks
  1st" is still reported for continuity. CLAUDE.md Validation is updated when evaluate.py lands.
- **OpenSky fallback (user, session 4):** if Trino historical access has not been granted by the time it
  is needed (P4.3 / P5.2), use the OpenSky datasets at https://opensky-network.org/data/scientific
  instead.

---

## 8. Amendment after Stage A (session 5): accuracy first, real data earlier, efficiency last

Written after `reports/stageA.md` (Stage A, all-on C n = 50, every config). The user set the priority order:
accuracy and functionality first, cost second. Nothing here changes a phase's content, a settled decision
(§4, §7), the V0/V1 comparison, the pre-registered re-ordering rule (§3.0, applied: written order, Phase 1
next) or the registry rule (P0.7). Phase 4 stays at full scale.

### 8.0 What Stage A changes
1. **Accuracy is low, and "unique" cannot be trusted.** All-on C:
   - V1: 7 / 50 correct, 7 confident-wrong. Only 7 of its 16 unique answers are right (44 %).
   - V1-asdesigned: 12 correct, 12 confident-wrong (12 / 27 unique right, also 44 %).
   - V0: 2 / 50.
   - Clean: 4 / 5.
2. **The provisional `inconsistent` status adds no information.** Against V1-asdesigned, which has the same
   rank-1 answers on 11 of its 12 correct seeds, it removed 5 correct and 5 confident-wrong answers alike.
   Precision of unique stays at 44 %; only the answer rate drops (27 → 16 / 50). Per the registry rule its
   threshold is not moved. P1.1 + P1.4 replace it and must beat it (8.3, U18).
3. **Detection sits at a cliff edge** (main-session check, 10 seeds, `reports/experiments/pathcheck/`).
   Synthetic events reach the detector at about 0–12 dB band SNR, many at 1–3 dB (the realism layer resamples
   each event to ≥ 0 dB, `realism.event_min_snr_db`). A ~1 dB change flips detection:

   | config | truth events detected |
   |---|---|
   | all-on | 19 / 20 |
   | sub:cwind | 17 / 20 |
   | sub:reflection | 14 / 20 |
   | loo:path | 10 / 20 |

   Stage A's `loo:path` "8 / 20 seeds detect nothing" is this effect, not a generator bug: ground reflection
   adds coherent energy, and the flat absorption model attenuates low harmonics more than ISO 9613 does.
   Consequences:
   - the "survival" failure classes mix detection losses with cascade kills;
   - leave-one-out deltas partly measure detection flips, not localization.
4. **Failures split three ways** (V1): survival at CPA 37 %, discrimination 37 %, bias 20 %. No single fix
   dominates, so the Phase 1 measurements, not a prior guess, should steer Phases 2–3.
5. **Compute is not the constraint at this scale.**
   - A full Stage A (V0 + V1, 275 units) took about 70 min on the PC (`--jobs 8`).
   - Scoring is about 95 % of V1's time.
   - Hypothesis explosions (up to 69,348 per clip) cause the timeouts: 2 / 50 all-on V1, which count as
     failures. So speed is a functionality issue only through timeouts.
6. **V0 (strict) is non-functional under realism.** 2 / 50, and the fallback arms don't beat V1. It stays a
   kept, cheap comparison arm (user requirement) but gets no variant-specific development.

### 8.1 Measurement fixes before any Phase 1 comparison (reporting only; settled)
- **Detection recall** per config: truth events detected / truth events. Already in rows as
  `truth_events_detected`.
- **A `detection` failure class**, reported beside the pre-registered taxonomy (the rule itself is
  unchanged): a run whose truth events were not all detected. That separates "the detector never saw it" from
  "the cascade killed it".
- **Conditional accuracy:** outcome tiers on runs where every truth event was detected, next to the
  unconditional figures. Localization changes are judged mainly on this; detection changes on recall.
- **Rank-1 accuracy ignoring status** (right pair and ≤ 200 m, whatever the label). This is the localization
  baseline that status changes must not lower: V1 ≈ 14 / 50 (7 correct + 7 labelled inconsistent).
- **Answer rate** (unique / runs) as a headline, next to precision of unique and correct rate.
- **Paired continuous tests** (rank-1 error, true-assignment rank, Δcost), which plan §3.0 already specifies.
  Stage A did not run them yet.
- **Hand-check ≥ 5 Stage A rows** against their kept truth and events before relying on these metrics.
- All of the above is re-summarised from the existing Stage A rows: no re-run.

### 8.2 Detection becomes an explicit accuracy item (new; U19, approved)
A detection miss caps accuracy no matter what Phases 1–3 do. Candidate algorithmic changes, none of which
moves `detect.trigger_db` or any other provisional threshold:
- a harmonic-summed tonal statistic (prop harmonic stacks, as `ridge.doppler_hough` already sums for seeding);
- a long-window (2–4 s) spectrogram pass for slow props (the doppler.txt "increase the resolution" step);
- a lower-band pass below the 100 Hz recording high-pass.

Measured on detection recall per config, paired, with clean and all-on. Also checked against false events
(the spurious-event count is in rows), because more detections mean more hypotheses and more discrimination
risk. The detection thresholds themselves are calibrated only on milestone 4.5 field recordings.

### 8.3 Order
1. 8.1 measurement fixes (re-summarise Stage A) and the hand-check.
2. **Phase 1**, as written. Acceptance for P1.1/P1.4 (U18): precision of unique above the status-ignored
   baseline (44 %), with rank-1 accuracy not lower, and answer rate reported.
3. **8.2 detection** (U19, approved), alongside Phase 1, because it acts on a different stage.
4. **Real data:** P4.3 real-traffic replay and P5.3 UAT coverage straight after Phase 1, and **field
   recordings requested now**. Stage A raises their priority: detection and the audibility levels can only
   be calibrated on real audio, and the synthetic level model is what puts events on the detection cliff.
   Minimum useful set: a few clips near a small airport, GPS location, accurate clock, ≥ 2 audible prop
   flyovers each. Data: Trino if granted, else https://opensky-network.org/data/scientific. Reported, never
   used to tune provisional values.
5. Phase 2 ladder → Phase 3 → milestone 6 → Phase 4 → Phase 5.
6. **Efficiency** is done only where it serves accuracy or functionality:
   - profile `score.rank_cells`, because hypothesis explosions cause timeouts;
   - identical-result speed-ups are adopted only if they reproduce Stage A rows exactly on ≥ 5 seeds;
   - result-changing speed-ups (top-K hypotheses per cell, a lower cap) are measured arms and the user decides.

### 8.4 Experiment design (accuracy over cost)
- **Stage A is the reference.** Later comparisons run only the new arm, paired by seed. This is valid while the
  V0/V1 code paths are unchanged; rows record commit and config hash.
- **Decision runs use the full config set**, because a full Stage A costs about 70 min. Bigger n where a
  decision rests on it: all-on C n = 100 (seeds 0–99) for Phase 1 acceptance. Stage A showed n = 20 per LOO
  config cannot separate arms (wide intervals).
- **Targeted subsets are screens only,** to rule an idea out quickly. They always include all-on + clean.
  Groups per change:

| Change | Groups beyond all-on + clean |
|---|---|
| R1 calibrated likelihood | source, path |
| R2 per-hypothesis silence | scene, levels, adsb |
| R3 unmatched-event ranking | adsb + a dropped-ADS-B config |
| R4 silence from audio | levels, scene |
| P3.1 track smoother | adsb |
| P3.2 baro correction | adsb with shared-QNH baro |
| P3.3 f0 drift | sub:rpm |
| P3.5 tolerance restructure | geometry, source |
| 8.2 detection | path, levels, scene, sub:reflection |

- **Never re-run only the seeds that failed in Stage A** (regression to the mean, missed new failures).
- **Screen on truth-derived ridges first** (`tests/test_cascade.ridge_events`), then the ablation.
- **Richer rows**, so a changed metric means re-summarising, not re-running.

### 8.5 New USER decisions: both APPROVED by the user (session 5)
- **U18 (P1.1/P1.4 acceptance):** may Phase 1's new status be adopted as the default only if, on all-on C
  n = 100 paired with Stage A, precision of unique beats the status-ignored baseline (44 %), rank-1 accuracy
  does not drop, and the answer rate is reported? **Approved.** It is an acceptance test, not a threshold:
  it tunes nothing.
- **U19 (detection item):** add 8.2 (algorithmic detection improvements, measured on recall and false
  events; thresholds untouched until 4.5)? It changes methodology step 2 (event detection). **Approved:**
  detection caps every later gain.
