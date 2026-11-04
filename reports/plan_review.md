# Adversarial review of reports/combined_plan.md

Reviewer, session 4. Read in order: CLAUDE.md, HANDOFF.md, combined_plan.md, the three skeptic reviews,
doppler_comparison.md, doppler.txt (as data), the designer's scripts, and the code where a claim needed it.
No repo file was changed except this report.

**What I re-ran** (scratch: `/private/tmp/claude-501/-Users-williambarr-qgis/c334e685-7499-4d45-9232-dd5dbcafcf8c/scratchpad/reviewer/`):
- `designer/d1_unmatched_evidence.py`: output in `reviewer/d1.out`.
- `designer/d2_bias_nuisance.py`: output in `reviewer/d2.out`.
- `designer/d3_dedupe.py`.
- `reviewer/r1_drift_prior.py`: checks whether D-2C's prior width matters.
- `run.json` timings of the skeptic's 18 realistic runs (`skeptic_system/runs/*/run.json`), to check the
  plan's compute estimates.
- Clopper–Pearson and McNemar numbers (scipy).

Every number D-2 and D-3 report reproduces exactly. D-1's numbers reproduce too, but they do not show what
the plan says they show (E1 below).

Citation keys follow the plan's: `[P-Fn]`, `[S-Fn]`, `[T-Fn]`, `[C-#n]`, `D-n`, and plan items `P0.1-a` and so on.

---

## 1. Must-fix issues (ranked)

### M1. P2.3 (evidence ranking) rests on evidence that doesn't test it, and its main failure mode is untested
- **Section.** §3 P2.3; §1.9; §4b decision 11; §0 D-1.
- **Problem 1: the winning partial isn't the truth.** D-1's "partial wins" isn't a recovery of the truth.
  In all three cases the best *partial* hypothesis is a different wrong location: a single-event locus on a
  background track. It is 9.5, 7.7 and 7.1 km from the mic (`reviewer/d1.out`: tracks `-/bg059`,
  `-/bg094`, `bg049/-`). The script prints `winner PARTIAL(true side)`, but that is a hard-coded label: the
  script never checks distance. So evidence ranking turns a confident wrong answer into an *uninformative*
  one (locus or abstain). That is still worth having, but it isn't "fixes S-F1".
- **Problem 2: D-1 tested a different mechanism.** D-1 tested a fixed per-point price (2, 4.5 or 22.5 Huber
  per point). P2.3 specifies something else: free straight-pass fit nats + ½·4·ln n_eff + (−ln p_u). The
  plan's "D-1 shows this flips 3/3" is borrowed evidence.
- **Problem 3: the dangerous direction is never measured.** The risk is the true matched event losing to
  its own free fit under realism. The free fit has 4 free parameters per event; the locked model has 1 (f0)
  plus a shared Δt. The P2.3 null price is tiny: BIC ≈ 2·ln 141 ≈ 10 nats, plus −ln 0.2 ≈ 1.6. Under realism
  the true cell's events have rms/σ 3–12 ([P-F1], run C). With σ re-estimated per hypothesis (P1.1), an RSS
  ratio of 4 at n_eff 50 already costs about 35 nats, more than the price. So P2.3 can turn most true
  matches into "unmatched", and most realistic runs into `locus`.
- **Problem 4: no multiplicity term.** The evidence has no prior on *which* track explains an event. With K
  candidate tracks there are K chances to match by coincidence, and the null has no look-elsewhere factor.
  That is exactly the S-F1 mechanism in dense traffic.
- **Problem 5: unbalanced sample.** D-1 uses 3 hand-picked cases from the 8 wrong E1 runs, with no
  intact-ADS-B control. Its ridges are clean (σ 0.2 Hz). Background traffic is placed ≥ 3.6 km from the mic
  (E1 builds this in, [S-F9.1]).
- **Evidence.** `reviewer/d1.out`; `e1_density.py` lines 34–40 (≥ 1.2 × threshold placement);
  `score._event_cost` (an unmatched event costs 0).
- **Fix.**
  - (a) Before building P2.3, rerun D-1 with the *specified* null price, on all 9 E1 `noadsb2` runs with K ≥ 60,
    *and* on intact-ADS-B all-on realistic runs (S-1a seeds).
  - (b) Report the fraction of true events left unmatched at the true cell, and the rate at which the true
    assignment becomes `locus`. Make "true events stay matched ≥ X % on intact realistic runs" a pre-registered
    acceptance criterion.
  - (c) Add the track-choice prior (≈ −ln of the number of class-compatible candidate passes for that event at
    that cell), or justify leaving it out.
  - (d) Correct the plan's text: D-1 supports *abstention*, not recovery.

### M2. The cheapest protection is under-ranked: the absolute fit test already catches 3/3
- **Section.** P1.4 (absolute consistency), Phase ordering, decision 11.
- **Problem.** D-1's own output says the absolute check (worst rms/σ > 2) flags the wrong full winner in
  3/3. That needs no new ranking rule, no p_u and no free-fit null. It is also the "top-right quadrant"
  per-event requirement the transcript uses (L600–605). It also matches HANDOFF's suggested *strict-first,
  robust-fallback* hybrid with an absolute-fit trigger ([S §2 hybrid 1]).
- **Gap.** The plan ties P1.4's absolute test to P1.1's calibrated σ, and puts the safety target
  (precision of "unique" ≥ 0.95) behind P2.3.
- **Fix.**
  - Pull a provisional absolute check forward into Phase 0/1 for all variants. Use a model-free scale:
    first-difference σ_white of the decimated ridge, corrected for lag-1 correlation.
  - Measure it in the baseline. Build P2.3 only if the absolute check plus P1.4 cannot meet the precision
    target.

### M3. Phase 0 is neither minimal nor sufficient; the baseline will hit known blow-ups
- **Section.** §3 Phase 0, P0.5, dependency order.
- **Not blocking the baseline:**
  - P0.1-c (velocity fallback). The synthetic ADS-B path never writes a null velocity
    (`realism.degrade_adsb` interpolates `ground_speed_mps` and `track_deg` for every fix; only
    `geometric_alt_m` becomes NaN for baro-only tracks). The fix doesn't affect the ablation.
  - P0.1-d (nodata). Every scenario uses FlatDEM.
  - P0.1-e. `locate` reads config; only callers that use defaults are affected.
  - P0.1-f. It normalises a per-cell penalty that P1.1/P2.1 replace a phase later, so it is throwaway work.
  - P0.3. See M8.

  These belong before milestone 7, not in front of the measurement.
- **Missing although blocking:**
  - **P2.5 (best-k enumeration).** The plan says "if the S-F6 blow-up appears, P2.5 moves ahead". It already
    appeared: 2 of 8 realistic C seeds took 432–502 s (C2: 7,488 hypotheses; C6: 14,606). In A0, 157–162 s
    went to the CPA/enumeration stage in *both* variants (`skeptic_system/runs/*/run.json`). The condition is
    met, so P2.5 belongs in Phase 0.
  - **A per-run wall-clock cap** in the driver, and recording timed-out runs as a failure class.
  - **The `cmd_locate` refactor.** "locate per variant, in-process" (P0.4) needs `cmd_locate`'s 380 lines
    (`cli.py:428`) split into stage functions with explicit I/O. That is unbudgeted work, and also the real
    HITL seam [S-F15].
  - **Running the slow test suite once.** HANDOFF says the rest of it wasn't rerun after the realism round.
- **Fix.** Phase 0 = P0.1-a, P0.1-b, P0.1-h, P0.2, P2.5, the stage-function refactor plus a disk-safe
  driver, and the slow suite. Move P0.1-c, d, e, f and g to before milestone 7, or to Phase 1.

### M4. The ~20 h baseline is underestimated about 2×, and its design has little power
- **Section.** P0.5.
- **Compute.** From the skeptic's run.json files, one all-on C scenario costs about 16 s synth + 155 s robust
  (mean; range 12–502 s) + 21 s strict ≈ **3.2 min**, not 2. A0 costs about 40 s + 214 s + 178 s ≈
  **7.2 min**, not 3–4. G has three events and more hypotheses, so it is at least A. Total: 220 × 3.2 min +
  110 × 7.2 min + 110 × ≥ 7.2 min ≈ **38–40 h** before any V2 arm, with a 500 s tail.
- **Power.** Per-cell Clopper–Pearson at n = 20 (C) and n = 10 (A, G) cannot rank LOO groups.
  - For 1/10, the one-sided 95 % upper bound is 0.39. Paired McNemar at n = 20 needs ≥ 6 discordant pairs,
    all in one direction, for p < 0.05.
  - The quoted bounds are one-sided without saying so: 20/20 → 0.861 one-sided, 0.832 two-sided; 18/20 →
    0.717 vs 0.683.
  - 20 seeds of `clean` are mostly wasted (known rank 1). A and G at 10 seeds can't support "≥ V1 rate per
    group" claims (Phase 2 and 3 "done").
- **Fix.**
  - Use a paired design: the same seeds across configurations, which P0.2's stream preservation is meant to
    allow.
  - Analyse it with paired tests on *continuous* metrics: rank-1 error, true-assignment rank, Δcost(truth −
    rank 1), and survival stage.
  - Put the seeds where failures are: all-on × 50 for C, so the failure taxonomy has ±7 pp precision.
    LOO × 20, paired; clean × 5.
  - State a quantitative decision rule for re-ordering Phases 1–3 (for example, "if ≥ 40 % of all-on
    failures are survival at CPA, P3.5 goes first").
  - Re-estimate the compute from a 5-scenario pilot before committing.

### M5. New provisional parameters will be tuned on synthetic data: the protection covers p_u only
- **Section.** P1.1, P1.4, P2.3–P2.5, Phase 2 "done", §5 risk 6, P4.1.
- **Problem.** CLAUDE.md forbids tuning provisional thresholds on synthetic data. Reserved seeds (P4.1) don't
  satisfy that rule: they protect against overfitting to *particular* seeds, not against tuning on synthetic
  data at all.
- **The new provisional knobs:**
  - `outlier_frac`, `p_unmatched`
  - `tie_quantile`, `gof_quantile`
  - `K_a`, the union top-M cap
  - `max_partial_per_cell`, the `weak` thresholds (σ_d/d > 0.5, σ_v > 50)
  - `max_bridge_s`, the baro-correction radius (50 km) and altitude bands
  - `silence.source_level_db`, `null_shifts_s`
  - the ADS-B budget σs (7 m, 0.25 m/s, 0.5 s)
- **What the plan sets against them.** Targets on development seeds:
  - "χ²₃ quantile in ≥ 80 % of clean runs" (P1.1);
  - "precision ≥ 0.95 with abstention ≤ 30 %" (P1.4);
  - "confident-wrong ≤ 1/40" (Phase 2).

  A missed target invites adjusting exactly these knobs. Only p_u gets "sweep and report, don't choose".
- **Fix.**
  - For every new knob, record an a-priori value with its source (physics, standard, literature, or
    "arbitrary") in config.yaml, marked PROVISIONAL, *before* the first measurement.
  - Report a fixed sensitivity grid for each.
  - State that a missed target is reported, never fixed by moving a provisional value.
  - Also: 1/40 confident-wrong has a one-sided 95 % upper bound of 0.113, so the target can't demonstrate
    ≤ 2.5 %. Say it is a point target.

### M6. V2 is a bundle of five changes plus one from a later phase; no effect can be attributed
- **Section.** Phase 2 V2 table; decision 4; Phase 2 and 3 "done".
- **Problem.**
  - V2 = calibrated likelihood + per-hypothesis silence + undetermined passes + evidence ranking +
    forced-detection silence + soft v/d gates. Its "Gates" row depends on **P3.5, in Phase 3**, yet Phase 2
    is "done when V2 runs end to end".
  - A bundled arm that beats or loses to V1 says nothing about which component did it. Wrong attribution
    later becomes wrong USER recommendations (decisions 3, 6, 11).
  - P2.1's own measure says "P3.6 must land together", but P3.6 is in Phase 3 in the dependency line.
- **Fix.**
  - Define V2 as an incremental ladder evaluated on the shared events: V1 → +P1.1 → +P2.1/P2.2 → +P2.3 →
    +P2.4, each a config flag. Use a factorial design for the two silence items if compute allows.
  - Drop the P3.5 dependency from V2's definition, and move P3.6 into Phase 2.
  - Phase 2 "done" compares each rung with the previous one, paired.

### M7. The circularity claims are overstated; P2.4 and P1.3 swap one self-consistency for another
- **Section.** P2.4 ("breaks the circularity in S-F9.1"); P1.3 (coverage targets); §2 "Where I differ" #4.
- **Problem.**
  - **P2.4.** P2.4's audibility uses a class source-level prior N(μ, σ_L) and ISO 9613 absorption *moved
    from realism.py*. The synthetic generator draws levels from N(class mean, 6 dB) with the same absorption
    code. Synthetic evaluation of P2.4 is therefore self-consistent by construction, just as the slant
    threshold was. CLAUDE.md already flags this for the decoys.
  - **P1.3.** The nuisance σs (latency 0.5 s, altitude 60 m) are the realism layer's own U(±0.6 s) and
    N(0, 60 m). Synthetic coverage of the ellipse is close to guaranteed, and says nothing about real ADS-B.
    The ellipse also leaves out RPM ramps, the largest bias (240–700 m [P-F1]). So the "85–95 % coverage on
    P0.5 runs" target will fail on the source group by design.
  - **"Curation doesn't rescue V0" (§2 #4).** This leans on S-1a, where the realism layer always places
    `n_near_traffic = 4` aircraft at 0.5–1.5× threshold in a 60 s clip. V0's 4/8 silence kills depend on that
    generator choice, just as V0's good E1 results depend on the opposite choice (traffic ≥ 3.6 km away).
- **Fix.**
  - State plainly that P2.4 and P1.3 can only be validated on real data (4.5 / P4.3). Use a different
    absorption and level model in the generator than in the pipeline (for example, add ground-effect excess
    attenuation and real-ground complex reflection only in synth).
  - Report P1.3 coverage per group, with an explicit "not covered: source changes" line.
  - Mark the V0-viability verdict as NEEDS-TEST under traffic-model placement (P4.2.1) and measured
    near-traffic density.

### M8. P0.3 is justified by a wrong runtime claim, and part of it edges toward HITL tooling
- **Section.** P0.3.
- **Problem.**
  - "About 30–50 % less runtime" is wrong. Event analysis is 3.5–9 s of 12–502 s per C run and 20 s of
    178–214 s in A0. Detecting once for both variants saves about **2–10 %** (C0: 6 s of 75 s; C2: 9 s of
    598 s).
  - The parts that add a `manual` provenance value and anticipate "an analyst's curated or traced events"
    are where CLAUDE.md's "no decision files … no analyst overrides" begins.
- **Fix.**
  - Keep `events --out` / `locate --events` as a stage I/O seam (allowed), with provenance limited to
    `detected` plus a sha256 and config-hash match. Anything else goes into `manual_inputs` automatically.
  - No `manual` enum and no editing affordances. Don't present it as a Phase 0 speed-up.

### M9. The success definition makes success depend on the method's own reported uncertainty
- **Section.** §4b decision 14.
- **Problem.** "Error < max(150 m, 2σ ellipse major axis)" rewards wide ellipses. A method that inflates its
  nuisance σs always "succeeds", and P1.3's σs are designer-chosen (M7). CLAUDE.md's criterion is the true
  location ranking 1st.
- **Fix.**
  - Keep a fixed-distance criterion: (i) true assignment at rank 1 *and* error < D, with D fixed a priori
    (e.g. 150 m or 200 m).
  - Report ellipse coverage *and* area as separate calibration metrics, never inside the success test.
  - Leave the choice to the USER as written, but present this flaw.

### M10. The user-requested sweep and HANDOFF's suggested hybrid are delayed or dropped without asking
- **Section.** P4.4; §3 V2; "Deferred".
- **Problem.**
  - HANDOFF orders the user's SNR / tolerance / DEM sweep right after the scoring robustness work. The plan
    puts it after Phases 1–3, the realism additions and the reserved-seed protocol: about 20+ working days
    later.
  - HANDOFF's "strict first, robust fallback" combination isn't tested at all. Yet in P0.5 both V0 and V1 run
    on every scenario, so the fallback rule (and S §2's absolute-fit-trigger version) can be evaluated
    **post hoc at zero compute**.
- **Fix.**
  - Add `V0→V1 fallback` (zero-survivor trigger) and `V0→V1 fallback (absolute-fit trigger)` as derived arms
    in P0.4's evaluator.
  - Run a reduced user sweep (SNR × 3, n_σ × 3, DEM bias × 3, paired seeds) right after Phase 1, or ask the
    user whether the delay is acceptable.

### M11. Rule conflicts the plan resolves itself
- **P3.1 (Kalman/RTS in local ENU).** CLAUDE.md: "local ENU is only for plotting and synthetic scenario
  construction". The plan extends that to "an internal numerical frame, documented", which changes the rule.
  Either run the CV / coordinated-turn filter in ECEF (CV is frame-agnostic; the turn axis is the local
  vertical computed from the ECEF position) or make it USER.
- **P2.1 / decision 16 (per-hypothesis silence as the default for V0 and V1).** HANDOFF: "keep the original
  design … any comparison should report both variants side by side". Changing V0's silence default (and
  P0.1-f/g changing V1's ranking) means "V0" no longer matches the design the user asked to keep.
  - Pin `V0-asdesigned` / `V1-asdesigned` configs (and a git tag) before Phase 0, and run them once on the
    baseline seeds.
  - Name the corrected variants V0′ / V1′. Changing what the user calls "V0" is USER-light.

---

## 2. Decision re-classification

| # / item | Plan says | Should be | Reason |
|---|---|---|---|
| 2a global c / wind not now | SETTLED | **USER (light)** | HANDOFF next step 2 lists the c search as a likely fix "needing user sign-off"; CLAUDE.md Unknowns sanctions a c range (`temperature_search_c` exists and is unread). Deferral rests on single-effect numbers in good geometry (4–43 m), but [P §2] shows a 2.4 m/s c change flips which side of the valley wins in run C. The plan's own thesis is "small systematics × degeneracy", so single-effect magnitudes don't settle it. Recommend defer, but ask. |
| Mic-height search deferred (unnumbered) | SETTLED (implicit) | **USER (light)** | Same: a HANDOFF-listed fix awaiting the user. |
| 16 per-hypothesis silence for V0 and V1 | SETTLED | **SETTLED as an option and for V2; USER-light for flipping V0/V1 defaults** | Changes the variants the user asked to keep (M11). |
| P3.1 filter in ENU | SETTLED (implicit) | **USER, or redesign in ECEF** | CLAUDE.md ECEF rule (M11). |
| 20 dedupe tolerance from fit covariance | SETTLED | **NEEDS-TEST, then SETTLED** | Under realism the covariance is overconfident (11/16 \|Δd\| > 3σ [S-F2]), so a covariance-only tolerance may under-merge one aircraft's two tone families. That creates spurious events, which kill V0 and use up V1's single unmatched slot. Decision 1 had a sys term; this has none. Gate the default on truth-event recall and the merge rate. Also, D-3's 44.8 % is *conditional* on CPA within 3 s and v/d within 15 %/35 %, so the real false-merge rate is far lower (never in the 15/45 s C scenarios). Priority is low. |
| 18 set fit_model_sys from synthetic quantiles? | USER | **SETTLED: no** | CLAUDE.md already forbids it ("don't tune them further against synthetic data"). |
| 2c per-track timing nuisance | USER | **SETTLED: report-only (P1.3); USER only if fitting it is ever proposed** | The recommendation keeps the locked rule as written, and only a change needs the user. |
| 14 success definition | USER | USER (keep), **but the recommendation must change** | M9. |
| 9 realism additions | SETTLED | SETTLED, except **the real DEM (3DEP) and any OpenSky hour are downloads: USER per item** | `data/` holds only the proj grids; P4.2.6 needs a download. Decision 22 covers it but P4.2 doesn't reference it. |
| P0.1-g refinement cap "bug fix" | SETTLED (Phase 0) | SETTLED, but **re-specify** | "Refine every cell whose centre-score lower bound can beat rank 1": no such lower bound exists (refinement can lower a score arbitrarily inside a valley). Use an explicit `refined` tier plus cap-hit reporting instead. |
| 21 evaluate.py + `cli.py ablate` | USER (light) | USER (light), **and it gates Phase 0** | CLAUDE.md lists both the layout and the subcommands; Phase 0 claims "no rule changes". |

No other SETTLED item changes a methodology step on inspection. P0.1-a/b/h, P0.2, P2.2-as-option, P2.5,
P3.2's structure and decisions 1, 7 and 13 are legitimately settled.

---

## 3. Evidence problems in the experiments

1. **D-1.**
   - The "PARTIAL(true side)" label is never computed; the partial winners are 7–9.5 km from the truth
     (`reviewer/d1.out`).
   - It tests a price structure that is not P2.3's.
   - Its 3 cases are selected from the failures, with no control.
   - The ridges are clean (σ 0.2 Hz, exact `predicted_ratio_at` ridges), and the background is ≥ 3.6 km
     from the mic.
   - Its conclusion "absolute check flags 3/3" is the real finding, and the plan under-uses it (M1, M2).
2. **D-2C** (drift halves ramp bias). It reproduces, and I checked it is genuine: β is well identified and
   independent of the prior from 1e-5 to 1e-2 /s. It reverts to 256 m only at σ_β = 1e-7 (`reviewer/r1`).
   Caveats:
   - One ramp shape (3 %/20 s at CPA + 5 s), one ramped event, L2 loss, true c, still air.
   - The residual reduced χ² after drift is still about 2.9 (cost 697 for 480 points at σ 0.15), so the ramp
     is far from absorbed.
   - The "cost" check uses the CRLB at the truth, but the real cost of β is lost *discrimination* between
     assignments. P3.3 does measure the gap, so keep that metric as the gate.
3. **D-2D** (systematic-aware ellipse). It reproduces (73 × 29, 262 × 51, 54 × 23 m). But it is linearised L2
   with the nuisance σs set equal to the realism layer's own draws (M7), and it leaves out ramps. P1.3's
   regression test "D-2D numbers ± 10 %" pins production code to a scratch L2 estimator. The production
   estimator uses Huber or calibrated nats, 0.5 s medians, the Δt prior bounds and in-cell bounds, so the
   test will fail or force the production code to mimic the scratch. Test with a Monte Carlo of the
   production estimator instead.
4. **D-2B.** It reproduces (397 m). The analytic 504 m lever is for parallel tracks; the plan says so.
5. **D-3.** It reproduces (44.8 %). It is a conditional rate (see decision 20), so it overstates the
   real-world impact.
6. **S-1a** (the plan's most-cited table): n = 8, one scenario type, `n_near_traffic = 4` fixed.
   - "Robust 3/8 correct" has a one-sided 95 % lower bound of 0.11.
   - It is a fine motivation, not a basis for SETTLED verdicts on V0's viability (M7).
7. **E1** (behind S-F1 and the P2.3 targets). Its geometry is idealised: straight tracks, truth-derived exact
   ridges, a flat world, and background traffic placed ≥ 3.6 km from the mic. "11/13 intact true rank 1" is
   an upper bound, not a realistic baseline for P2.3's "stays ≥ V1" target.
8. **P1.1 test statement.** "2·Δnll between truth and a *wrong cell* follows χ²" is wrong. Wilks applies to
   truth vs the *MLE*: 2Δnll ~ χ²₃ for (E, N, Δt) with f0 profiled, and only when the optimum is interior
   (not at the Δt prior edge or a cell bound). The "Measure" bullet states it correctly; fix the test.
9. **P1.1's σ_white = var(Δf)/2.** For correlated ridge noise this is σ²(1 − ρ₁). At the measured ρ₁ of
   0.26–0.65 [P-F3.3] it underestimates σ by 14–41 %, which biases the floor low, exactly where the floor
   matters. Correct for ρ₁, or use residuals about a local smooth.
10. **Bounded loss −ln(ε + (1 − ε)·N(z)).** It mixes a probability with a density. The outlier term needs a
    density, ε / (band width in σ units), or the "unmodellable = outlier price" identity is unit-dependent.
11. **The P2.3 "reject event" seam is mis-specified.** Setting p_u = 1 makes −ln p_u = 0, so a locked match
    can still win. "Reject event" should mean removing the event from the input file (P0.3's seam), not a
    prior.
12. **The compute and runtime claims** (M4, M8) are contradicted by the skeptic's own run.json timings.

---

## 4. Should-fix and nice-to-have

**Should-fix**
- **Under-weighted: silence between events [T §3; L326–347].** It is rejected with "V2's forced detection
  already handles it", but never tested. It is a one-line time mask, and the most believable non-detection
  interval. As an option on V0 it could rescue some of V0's silence kills cheaply. Add it as a baseline
  toggle.
- **Under-weighted: [C-#8] per-pass kinematic tolerance** from the ADS-B ground-speed spread around CPA. It is
  data-derived, not tuning, and available now. It replaces part of the 15 m/s allowance for turns and speed
  ramps legitimately. P3.5 drops it without adjudicating it.
- **P2.2 before P3.1 builds a temporary "undetermined gap" mechanism** that P3.1's gap bridging supersedes.
  Either do a minimal gap bridge first, or accept P2.2 as throwaway and say so.
- **P5.3 (UAT coverage) and the milestone-4.5 data request are too late.** p_u (P2.3), the drop rate (P4.2),
  source levels (P2.4) and fit_model_sys (P3.5) all hinge on them. Ask the user now when field recordings and
  the coverage downloads are possible; schedule P5.3 in parallel with Phase 1.
- **Missing: a clock/Δt-prior misspecification diagnostic** [S open Q5]. A wrong ±10 s prior silently
  empties the CPA stage. Run the offset filter once with a wide prior and report where the feasible-Δt mass
  lies. It is cheap and needed for milestone 7.
- **Missing: is OpenSky geoaltitude HAE or MSL?** [P open Q2, S open Q4]. A 16 m error at Boulder. Check it
  against runway elevation on short final in the first real OpenSky pull.
- **Missing: the realism layer's reflection model** is real and positive (R 0.8–1.0). Real grazing ground
  gives R → −1 with phase [P-F14], and water gives R ≈ 1. The `path` LOO group therefore measures a partly
  artificial effect. Fix the generator before the baseline, or mark the path group as provisional.
- **Missing: US-wide storage.** There is no estimate for the CONUS DEM (a 30 m CONUS DEM is several GB;
  windowed reads still need the tiles on disk) or for a CONUS OpenSky hour (hundreds of MB raw). The dense
  TrackSet at 1 h ≈ 3.6 GB RAM for 5,000 tracks. With 12 GB free this needs a budget and user approval per
  download (P5.1/P5.2).
- **Missing: milestone 6** (exports + QGIS MCP) isn't placed. P1.2 adds layers, an ellipse and a fine
  raster that need styles and template updates. Say whether milestone 6 happens before Phase 5.
- **S-F13** (window and relative time modes: dense TrackSet, about 44 GB for a day) and **S-F14** (terrain
  slope inside a cell, undeclared nodata values) aren't adjudicated. They can be deferred, but say so.
- **New config keys aren't all listed:** the 50 km baro radius, the altitude bands, the union top-M cap, the
  `weak` thresholds, `max_bridge_s`. Each needs a config entry and a PROVISIONAL mark (CLAUDE.md "every
  threshold in config.yaml").
- **Reproducibility of the deleted scenarios.** Record git commit + seed + config hash per ablation row, and
  keep tracks.parquet (0.36 MB) for failing rows only. Regeneration after a code change is otherwise not the
  same scenario.
- **P0.2 is more than half a day.** `srng` draws conditionally (`if rng.uniform() < p.rpm_ramp_prob:` then
  further draws), and groups share streams (`rrng` covers several groups). Group toggles that skip a
  draw shift later draws. Per-group, per-aircraft streams are needed (`default_rng([seed, group, k])`),
  and HANDOFF's single-seed numbers will no longer reproduce.
- **P1.4's held-out-event check** applies only to N ≥ 3, which is rare in the 60 s benchmark. It is low
  priority.

**Nice-to-have**
- **P1.2 fine 40 m raster** and the per-event scatter: keep, but after the core items.
- **P3.1:** consider a smoothing spline with velocity fusion and covariance before a coordinated-turn KF/RTS.
  It is lower risk and the same information for scoring.
- **P2.4:** stage it. First the cheap measured-floor P(audible) ([C-#10]), then forced detection only if the
  ladder (M6) shows it is needed.
- **The plan says "variant" everywhere but doesn't specify the config mechanism.** Use one `variant:` key, or
  named config overlays recorded in run.json, so arms are reproducible and visible to a later analyst.

---

## 5. What the plan gets right and should keep

- **Adjudication discipline.** Every critical and major finding is tabled with a verdict, and NEEDS-TEST is
  used honestly (P-F9, P-F14, S-F8).
- **Its corrections of the earlier opinion are right and evidence-based:**
  - σ_model as calibration, not a location fix;
  - c/wind deprioritised (but ask; see table §2);
  - the latency lever in corridors underestimated by [C-#13];
  - tiered anchors demoted.
- **Locked scoring is preserved throughout.**
  - P1.3 *reports* the per-track timing effect instead of fitting it.
  - P2.3 correctly keeps the free-fit t0 out of every Δt.
  - β in P3.3 is pivot-invariant with f0 free.
- **The bug fixes are real.**
  - P0.1-a: reproduced, 6/6 false invalids.
  - P0.1-b: `prepare_cascade` lines 640–647, `if ix.tree is None: active[:] = False`.
  - The gof no-op [P-F3.2]: fit.py l.168 vs l.208.
  - The 6000 m defaults: cascade.py:54, synth.py:103.
- **P0.1-h (config diff + hash in run.json).** It makes the automation claim auditable without building HITL
  tooling.
- **P0.4's evaluator and failure taxonomy** (survival / discrimination / bias, along- vs cross-track error,
  Δt vs clock error) is the right instrument. Its disk-safe delete-as-you-go driver with a free-space guard
  respects the 12 GB limit.
- **P1.2 imports "best per flight pair"** from the transcript at the right place. P1.4's statuses never remove
  candidates (no withholding; decision 15 recommends "never withhold"). Both are good HITL seams.
- **Transcript imports.**
  - Brightness is imported only with normalisation and a time-shift null [T-F3].
  - Analyst curation is treated as a milestone-9 input, not built now.
  - The hand-fit, uncalibrated brightness, n = 1 validation and altitude-datum errors are rejected.
- **The Phase 4 protocol** (frozen config hash, reserved seeds, decoupled traffic and silence truth,
  unprotected missing tracks, real DEM, real traffic replay) answers [S-F9] properly, provided M5 is added.
- **§5's risks are candid:** calibration may fail, UAT coverage, corridor physics limits, tuning creep,
  disk.
