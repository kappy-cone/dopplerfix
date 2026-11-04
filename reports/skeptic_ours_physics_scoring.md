# Skeptic review: physics, per-event characterization, ridge/detection, scoring

Reviewer scope: physics correctness, fit.fit_ridge, ridge/detect failure modes, score.py cost function and
free parameters, and the realism gap (all realism on → true cell rank 5–39). Another review covers
cascade/system/validation/scale. Independence: I did not open doppler.txt or anything else under reports/.

All scripts are in
`/private/tmp/claude-501/-Users-williambarr-qgis/c334e685-7499-4d45-9232-dd5dbcafcf8c/scratchpad/skeptic_physics/`
(run as `cd /Users/williambarr/qgis && PYTHONPATH=.:<that dir> .venv/bin/python <script>`). No repo file was
edited; pipeline runs wrote only to that scratch directory (scratch config with `outputs.root` redirected).

Evidence labels: **VERIFIED** = computed or tested here; **CODE-READ** = from reading code; **SUSPECTED** =
reasoned, not tested.

Key experiments referenced below:
- `bias_budget.py` / `bias_huber.py`: two straight tracks, hidden mic, ridges synthesised with one perturbed
  effect at a time (exact uniform-wind propagation, true c, true mic height, source ramps, ADS-B latency/baro
  error), then fitted with the pipeline's nominal model (still air, config c, 1.5 m mic, ADS-B track as given,
  constant f0 per event, one shared Δt). L2 (`bias_budget`) and the pipeline's Huber k=3σ with the ±10 s Δt
  prior (`bias_huber`). σ = 0.15 Hz per 0.5 s point, 240 points per event (±60 s).
- `fit_honesty.py`: Monte Carlo of `fit.fit_ridge` (60 trials each).
- Pipeline run of scenario C, seed 0, all realism (`synth --duration-s 60 --event-times 15,45`, then `locate`),
  reproducing HANDOFF: **true cell rank 5, rank-1 error 261 m**. Same seed with `rpm_ramp_frac: [0, 0]` (this
  keeps the random streams identical; only the ramp amplitude becomes 0): **rank 4, 232 m**.
- `diag.py` / `diag2.py`: the locked cost surface around the true mic with the *true* assignment, Δt profiled
  on a 0.05 s grid, under counterfactual inputs (clean ADS-B from a same-seed rebuild, true c, true mic height,
  a fix to the Newton seed, σ override).
- `spec_dump2.py`: recovers the per-event hidden draws (ramps, twin detune, turns, accel) that truth.json does
  not record.

---

## 1. Ranked findings

### F1. Unmodelled source-frequency change is confounded with geometry; constant f0 per event turns a few-percent RPM change into hundreds of metres of bias, and the cascade's wind allowance hides it. **Critical.**
*Claim.* Doppler and an RPM change both scale every harmonic by the same factor, so harmonics cannot separate
them. A ramp lasting 10–40 s overlaps the Doppler transition time d/v (10–25 s). With one constant f0 per
event (score._event_cost profiles a scalar f0), the scorer can only absorb a ramp by moving the mic and Δt.

*Evidence (VERIFIED).*
- `bias_huber.py`, crossing tracks at 90°, ramp on event 0 (tanh, as in realism.rpm_profile):
  3 %/20 s centred at CPA+5 s → **242 m** bias, Δt +3.3 s; 10 %/20 s → **706 m** (Δt pinned at the +10 s prior
  edge). Same-direction corridor: 700 m for either. A 3 %/40 s ramp on the approach plateau: 30–60 m (a ramp
  away from the transition is mostly absorbed by f0). For scale, every other single effect below gives 3–75 m
  in crossing geometry.
- `fit_honesty.py`, 3 % ramp 5 s after CPA: fit_ridge biases are −8.3σ in v (6.0 m/s), −3.4σ in d (79 m),
  −9.6σ in t_rx (0.94 s), and the gof factor is 0.977 (no widening, see F3).
- Pipeline run C seed 0 (`spec_dump2.py`): event 2 (DH8D, truth v 82.5 m/s, d 812 m, CPA rx 45.00 s) has an
  RPM ramp of −8 % centred 9 s before emission CPA, duration 34 s. Fit: **v 97.8 ± 1.1** (14σ, +15.3 m/s),
  **d 1044 ± 30** (7.7σ), t_rx 44.27 (true 45.00). The cascade still passes it only because
  `tol_v = hypot(3·σ, wind_sys_mps=15) = 16.3 m/s` (run output). Rank-1 per-point score 10.1, event rms 3.0 Hz.
  With the ramp amplitude set to zero (same streams): v 80.5 ± 0.9, d 793 ± 22, score 1.87, rms 0.65 Hz.

*Failure scenario.* A Cessna reduces power during the pass (−5 % RPM over 20 s near CPA) → ridge drops
faster than Doppler alone → fit says faster and farther; the locked scorer moves the mic 200–700 m along the
(position, Δt) valley; the true cell drops out of rank 1, with nothing in the outputs explaining why.

*Real-world caveat.* Constant-speed propellers, turboprops and helicopter rotors are governed (RPM within
about 1 %); fixed-pitch GA aircraft changing power and jets changing N1 are not. Realism's 30 % × 3–10 % may
be pessimistic away from airports and optimistic near them. That needs measuring in milestone 4.5.

*What would help (not a design decision).* (a) A per-event consistency check: once a track is assigned, its
ADS-B ground speed fixes the plateau ratio (c+v cosθ)/(c−v cosθ). A ramp shows up as a mismatch between
observed and predicted swing. Flag or down-weight that event rather than letting it move the solution.
(b) A low-order drift term with a prior. This is only partly identifiable, and a free spline would absorb the
location information. (c) A bounded loss (F5) so a ramped half-ridge stops dominating.

*Cheapest confirmation.* Rerun A/C/G with all realism and `rpm_ramp_frac: [0, 0]`, which keeps the streams
identical. For C seed 0 this moved the true cell from rank 5 to rank 4 and the error from 261 m to 232 m, so in
C the ramp was a large *score* effect but not the only *location* effect (see F3/F4).

### F2. Position ↔ Δt degeneracy: same-direction corridor geometry amplifies small ADS-B errors into 400–500 m, bounded only by the clock prior. **Critical.**
*Claim.* An event's received CPA time depends on the mic's along-track position through −û·δP/v. With two
near-parallel same-direction tracks at similar speeds, moving the mic along the corridor shifts both CPA times
almost equally, and the shared Δt absorbs that. The curve shape constrains only slant distance, not along-track
position. The along-track coordinate is then fixed only by 1/v₁ − 1/v₂ and by the Δt prior
(±`clock_tol_s` = 10 s → ±v·10 s ≈ ±600–700 m).

*Evidence (VERIFIED, `bias_budget.py` / `bias_huber.py`).* CRLB 1-σ major axis: 2.2 m (crossing 90°), 9.4 m
(same direction +10°, 60/70 m/s), 18.5 m (same direction, equal speeds), 1.5 m (reciprocal). Bias from the same
inputs:

| effect | crossing 90° | same-dir +10° | same-dir, equal v | reciprocal |
|---|---|---|---|---|
| ADS-B latency +0.6 / −0.6 s | 46 m | **397 m** (Δt −6.2 s) | 88 m | 39 m |
| baro offset +60 m on one track | 64 m | 72 m | **521 m** (Δt −8.6 s) | 50 m |
| latency + baro (Huber) | 75 m | **468 m** (Δt −7.2 s) | – | 64 m |
| c +3 m/s | 10 m | 43 m | 30 m | 9 m |
| wind 10 m/s | 14–20 m | 8–27 m | 21 m | 4–26 m |
| true mic 10 m (model 1.5 m) | 4.5 m | 12 m | 17 m | 4 m |
| ADS-B speed +0.25 m/s (1 kt step) | 3.6 m | 20 m | 14 m | 2.8 m |
| combined (walk+lat+baro+c+wind+mic, Huber) | 99 m | **611 m** (Δt −9.5 s) | – | 91 m |

In the same-direction latency+baro case the minimum's mean Huber is **0.007 per point**, against 3.256 at the
truth: a nearly perfect fit 468 m away.

realism.perturb_kinematics makes 50 % of later events follow the first event's heading ±20°, half of them
reciprocal. So about 25 % of event pairs are same-direction, the degenerate case. Each group alone
(geometry only, or ADS-B only) is benign. Together, the geometry multiplies the ADS-B errors. That fits
"each group alone rank 1–2, all on rank 5–39" (section 2).

*Failure scenario.* Two GA aircraft on the same VFR corridor 4 minutes apart; one ADS-B feed lags 0.5 s → the
answer slides 300–500 m along the corridor. Silence and the cascade cannot catch it, because every cell on the
corridor explains both events.

*Cheapest confirmation.* Ablate `corridor_frac` (0 vs 1) with ADS-B degradation on, same seeds, and report the
along-track and cross-track components of the rank-1 error, and Δt error against −clock_error_s. Also: compute
the cost Hessian at rank 1 (3×3 in E, N, Δt, f0 profiled) and report its eigenvalues. A near-zero eigenvalue
identifies these cases automatically.

### F3. Event weights and σ are not noise estimates: σ is the straight-line misfit, gof widening is a no-op, and the Huber sum is overconfident. **Major.**
*Claim.* `cascade.observed_events` sets `ridge_w = 1/noise_hz²`, and `score.prepare_ridges` uses
σ = 1/√median(w) = `EventFit.noise_hz`. In `fit.fit_ridge` (line 168) that is `s_in = std(stage-1 residuals of
the straight-line fit on inliers)`. It is *not* the white part, despite the docstring at fit.py:68.
Consequences:
1. Events whose kinematics break the straight-line model (turns, climbs, speed ramps) get σ inflated and are
   down-weighted in scoring, even though the scorer's general track model could fit them. **VERIFIED**: run C
   seed 0 event 1 (turn 2.16°/s, speed ramp −6.7 %, edge-clipped) has σ = 0.83 Hz; event 2 has σ = 0.19 Hz.
   That is a 19× weight ratio in z², so the solution is effectively the single-event locus of event 2. Event 2
   is nearly overhead (horizontal offset 50 m), where ∂d/∂(cross-track) = x/d ≈ 0.06, so it barely constrains
   cross-track position either.
2. `ObservedEvent.gof = max(1, rms_hz / noise_hz)`, but rms_hz and noise_hz are residual RMS/std of the *same*
   fit (stage 2 vs stage 1), so gof ≈ 1 by construction. **VERIFIED**: median gof 1.000 (white noise), 0.996
   (correlated), 0.977 (3 % ramp), 0.995 (clipped). `cascade.gof_widening: true` changes nothing.
3. The decimated points are neither independent nor at σ. **VERIFIED** (`ridge_corr.py`, run C): event 2's
   scorer σ is 0.19 Hz, but its 0.5 s median-decimated residual std is 0.33 Hz with lag-1 autocorrelation
   0.26. Event 1: lag-1 0.65, lag-4 0.36. Median decimation of correlated 0.1 s frames does not reduce
   variance (raw 0.32 → decimated 0.33). The Huber sum therefore over-counts information by roughly 3–6×,
   before counting model error.
4. ADS-B model error is not in σ at all. **VERIFIED** (`adsb_jitter.py`): PCHIP through 7 m-noise fixes gives
   modeled-curve errors of 0.16 Hz RMS within ±15 s of CPA (150 Hz, 780 m slant). With 3 % MLAT fixes at
   100 m: 0.39 Hz RMS, spikes of 6 Hz. Both are comparable to or larger than ridge σ, and far above the
   0.05 Hz floor.

*Failure scenario.* Any event with a turning aircraft → σ ×4 → the other event dominates → near-single-event
locus.

*Measured effect on location (VERIFIED, diag2).* In run C seed 0, overriding event 1's σ from 0.83 to 0.25 Hz
moved the cost minimum only from 211 → 197 m (base inputs) and from 266 → 249 m (clean ADS-B, true c and h).
So the mis-weighting is real, but it was *not* the main driver of the 200+ m error in this case (section 2).
Its bigger cost is statistical: the Huber sum is not a likelihood, which breaks F6, F7 and F12.

*Fix direction.* Set σ_e from residuals about a local smooth (or about the best hypothesis's modeled curve,
one iteration), add σ_model² from the ADS-B noise budget, and divide each event's Huber sum by its effective-N
factor (1 + 2Σρ_k).

### F4. Bug: `score.modeled_ratio` falsely declares points unmodelable within r/c of every track-segment end. **Major.**
*Claim.* The Newton seed samples the track at the *received* time (score.py:134,
`A, _ = ts.sample(k, tr)`). If tr is past the segment end, the seed is NaN and stays NaN, so the point counts
as missing (penalty Huber(3k) = 22.5) even when its emission time t_r − r/c is inside the track. Segments
end at every ADS-B gap longer than `tracks.max_gap_s` (20 s). Realism draws 0.2 gaps/min of 5–60 s per
aircraft, events included (`protect` covers only dropping).
*Evidence (VERIFIED, `edge_bug.py`).* Track ending at t = 100 s, mic at 1.28 km: received times 100.0–105.5 s
have exact emission times 91.8–96.5 s, inside the track, yet the scorer returns `valid=False` for all of them.
In run C seed 0 (base inputs), **5 ridge points of event 1 are "missing" at the true mic**. Track f8829a has a
22 s ADS-B gap from 57 s, so its segment ends at 58.26 s. The points are received at 58.4–61.2 s, and their
exact emission times are 48.9–51.3 s, inside the segment (`miss_dbg.py`). With clean ADS-B there are 0.
*Measured effect (diag2, clamped seed).* The missing count at truth drops 5 → 0, and the per-point cost drops
2.667 → 2.077 at the truth and 1.809 → 1.447 at the minimum. The minimum's *location* is unchanged (211 m,
Δt +0.66 s), because nearby points of the same hypothesis lose about the same number of points. So in this
case the bug inflates costs without moving the estimate.

It matters when hypotheses with different track-end exposure are compared: a true track whose segment ends
inside the event against a decoy whose doesn't. Each false miss costs 22.5, about 10× a typical modelled point
here. The count also depends on r (r/c seconds), which favours mic positions closer to that track.
*Fix (tested in `mrfix.py`).* Seed (and iterate) with the sample time clamped to [t_first, t_last − ε] of the
segment. Only declare a point missing if the *converged* t_e is outside coverage. Clamping exactly to t_last
still returns NaN, because `TrackSet.sample` needs sample i0+1.
*Related (CODE-READ):* the same aircraft's other segment cannot be used for the remaining points, because one
hypothesis gets one TrackSet index. A gap split mid-pass therefore makes half the ridge "missing", at 22.5 per
point.

### F5. The unmodelable-point penalty is inconsistent with the unbounded Huber loss. **Major.**
*Claim (CODE-READ + numbers).* A modelled point with |z| = 12 costs 3·(12 − 1.5) = 31.5, more than an
unmodelable point (22.5). Under systematic misfit (ramps: rms 3 Hz at σ 0.25 Hz in run C, so z ≈ 12), "the
track has no data here" becomes cheaper than "the track is there but the curve is off". Hypotheses using
segments that miss part of the ridge, or wrong tracks that cover less of it, gain an advantage. A *missing*
track during an observed event should be evidence *against* that track, not neutral.
*Fix direction.* One per-point likelihood with an explicit outlier/mixture component (bounded loss, e.g.
−log(ε + (1−ε)·N(z))), with "unmodelable" priced at least at the outlier level.
*Experiment.* In a scenario where the true event track has a mid-pass gap, compare the cost of the true
hypothesis with that of a decoy covering the whole ridge.

### F6. The probabilistic-silence penalty adds nats to a quantity that is not a log-likelihood; in practice silence does not affect ranking. **Major.**
*Evidence (VERIFIED from run C).* The silence penalty is 2.1 nats ÷ ~200 points ≈ 0.01 per point. The
rank-1 vs rank-2 gap is 10.144 vs 10.221 (0.08 per point); the no-ramp run gives 1.866 vs 1.881. Given F3
(σ too small, correlated points), the Huber sum is inflated by a factor of 3–6 or more, so a nat of silence is
worth 3–6× less than intended. If σ were inflated instead, the reverse would hold. The weighting is accidental
in both cases.
*Also (CODE-READ).* (a) The penalty is computed once per cell, at the cell centre and at the *interim* best
hypothesis's Δt midpoint (cascade.run_tile), not at the refined point or the scored hypothesis's Δt.
(b) In `silence_explain: cpa`, a pass is "explained" if that track passed the CPA prefilter for *any* event at
the cell, not if the scored hypothesis assigned it. Two aircraft that are both CPA-consistent with one event
excuse each other, even though only one can produce it.
*Fix direction.* Turn the curve cost into a calibrated log-likelihood: per event, profile σ (−loglik ∝
(n_eff/2)·ln RSS) or scale by the residual variance at the best hypothesis. Then nats from silence and from
curves are commensurate. Compute silence per hypothesis.

### F7. Lexicographic ranking (n_unmatched, cost) lets one spurious event override any amount of curve evidence. **Major.**
*Claim (CODE-READ, score.CellScore.rank_key; cascade.offset_filter).* Any hypothesis that matches one more
event ranks above every hypothesis that leaves it unmatched, regardless of cost. The tolerances are wide:
v ±15–16 m/s (wind_sys), t ±3–8 s, d ±hundreds of m plus 141 m grid term (run C: d tolerance ±733 m for event
1). A spurious event (car above the 25 m/s floor, an aircraft without ADS-B, an undeduplicated half-order or
twin tone) can therefore be "explained" by some decoy or background track at some cells. Those cells then
outrank the true cell, which correctly leaves the spurious event unmatched.
*Experiment.* Add one extra synthetic tonal event (Doppler curve of an aircraft absent from ADS-B, plausible
v and d) to a clean scenario that ranks 1st, and count how many cells outrank the truth lexicographically.
*Fix direction.* Price an unmatched event in the same likelihood units: −loglik of the ridge under a
"no-track/background" model, plus a prior probability of an ADS-B-less source. Do not rank by count first.

### F8. Track interpolation passes position noise straight into the model, and a single NaN velocity row switches an aircraft to derivative velocity (catastrophic). **Major (real data).**
*Evidence (VERIFIED, `adsb_jitter.py`).* tracks.interpolate_track uses PCHIP *through* every fix, with no
smoothing. MLAT-grade fixes (100 m) pass `outlier_m: 300`. With `velocity_source: adsb`: 0.16 Hz RMS near CPA
(7 m noise), 0.39 Hz RMS and 6 Hz spikes with 3 % MLAT. With derivative velocity: **4.2 Hz RMS near CPA (7 m
noise) and 365 Hz RMS / 81 kHz spikes with MLAT**. tracks.tracks_from_frame (line 154) falls back to
derivative velocity for the *whole aircraft* if *any* row lacks ground_speed or track_deg. OpenSky state
vectors routinely have null velocity rows (position and velocity come from different messages), so at
milestone 7 this would silently break scoring for those aircraft.
*Fix direction.* A constant-velocity/constant-turn Kalman–RTS smoother using positions and reported velocities
together, per-row fallback, and MLAT down-weighting. Its covariance also supplies σ_model for F3.

### F9. Ridges are extracted conditional on a straight-pass model, and the scorer inherits that selection bias. **Major.**
*Claim (CODE-READ).* fit.characterize_window seeds with a straight-pass Hough transform (doppler_hough: (v, τ)
grid of straight curves). It then re-extracts the ridge by `guided_ridge`, taking the strongest bin within
±`guide_hz` (3 Hz prop, 8 Hz jet) of the *straight-line fit*. The DP limits upward motion to 4 Hz/s. A turning
or climbing aircraft departs from the straight curve by more than 3 Hz exactly where the general model would
discriminate. Those segments are dropped or replaced by noise or another tone within the guide band, and the
scorer receives a ridge biased toward the straight shape. A 3°/s turn changes the ratio at up to v·ω/c ≈
0.9 %/s, i.e. ≈ 6.8 Hz/s at 750 Hz, beyond `max_up_hz_per_s: 4`.
*Experiment.* For turn-rate 3°/s scenarios, compare ridge coverage and residuals against the truth curve, and
re-extract the ridge guided by the assigned track's modeled curve for the top-N cells.

### F10. Wind and speed-of-sound physics: the CLAUDE.md wind claim is right, the travel-time figure is low, the allowance is far too large, but it is masking F1. **Minor (physics), Major (consequence).**
*VERIFIED (`wind_check.py`, exact uniform-wind ray solution |P − A − wτ| = cτ).* Closed-form inversion of the
exact asymptotes gives v = 59.745 m/s (10 m/s tailwind), 60.358 (headwind), 60.024 (crosswind) for 60 m/s
ground speed. So "Doppler v tracks ground speed" holds to ±0.4 m/s. The synth's first-order model
(c_eff = c + w·k̂ along the straight ray) matches the exact ratio to 1e-4.
CLAUDE.md's "travel-time ~0.03 s/km per 10 m/s" is low: the effect is r·(w·k̂)/c², up to 0.085 s/km (56 ms/km
in the test geometry).
Location bias: wind 4–27 m, c ±3 m/s 9–43 m, mic height 10 m vs 1.5 m 4–17 m (bias table in F2). So
`wind_sys_mps: 15` is ~40× the wind effect on v. But the run C ramp case shows v fit errors of 15 m/s from
source changes, which this allowance is silently absorbing. Tightening it without F1 will reject true cells.
*Unmodelled but bounded (SUSPECTED).* In a stratified atmosphere the Doppler factor uses the horizontal
slowness, which is invariant along the ray (Snell): effectively c and wind *at the aircraft's altitude*. A
6.5 K/km lapse over 800 m changes c by about 3 m/s, and wind at altitude is typically 1.5–2× the 10 m wind.
This is the same order as the c and wind rows above, i.e. tens of metres.
*Implication for HANDOFF step 2.* A speed-of-sound search and a mic-height search buy ~10–40 m and ~5–17 m.
They are low priority compared with F1–F5. If c is searched, give it a tight prior (±5 m/s): with v fixed by
ADS-B, c only changes the curve asymmetry, which is the same signature a ramp produces.

### F11. Per-event fit covariance: conservative for white noise, honest for correlated noise, meaningless under ramps. **Minor.**
*VERIFIED (`fit_honesty.py`).* std(error/σ) for (v, d, t_rx): 0.28 (white: the √(window/hop) = √10 floor
inflates σ 3.5×), ≈1.0 (correlated frames), ≈1.0 (edge-clipped, coverage 0.4τ). Under a 3 % ramp the errors
are 3–10σ biased (F1). The covariance → t_rx propagation (cascade.observed_events J matrix) is correct.
*Note.* `f0_drift_prior_per_s: 1e-4` (1σ) is 17× smaller than a 10 %/60 s ramp. Drift is bounded at ±50σ, so
the drift nuisance cannot absorb realistic ramps and only marginally widens σ_v.

### F12. The ambiguity measure is scale-dependent and fails in the most dangerous case. **Minor–Major.**
*Claim (CODE-READ score.ambiguity; numbers VERIFIED in F2).* "Tied" means score ≤ best·(1 + 0.15). Score
units depend on σ (F3), so 15 % can mean an enormous or a negligible likelihood difference. When a degenerate
geometry yields a near-perfect wrong fit (best ≈ 0.007 per point, truth 3.26), the tie band is ≈ 0.001 wide,
nothing ties, and the result is labelled **"unique"** while 468 m off. In run C the 261 m error is inside the
400 m radius, so it is "unique" there too. That label is reasonable only by coincidence.
*Fix direction.* Ties from a calibrated Δloglik (χ² quantile), and an error ellipse from the Hessian at
rank 1 (F2).

### F13. Missing time-scale nuisance for long clips (recorder clock skew). **Minor.** SUSPECTED.
Consumer recorders run 10–100 ppm off nominal. One Δt cannot absorb a skew of 0.04–0.36 s over 15–60 min,
which is comparable to timing tolerances and to the timing errors that already move solutions (F2). Frequency
scaling is absorbed by the per-event f0. Add Δt(t) = Δt₀ + κ·t with a tight prior for clips over ~10 min.
Experiment: resample a synthetic 1000 s clip by 1+5e-5 and rerun F (920 s offset).

### F14. Twin-engine detune and ground-reflection interference produce frequency steps and wobbles comparable to the effects that move the solution. **Minor–Major.** SUSPECTED.
- Twin detune of 0.2–1 % puts two equal-amplitude tones 0.3–1.7 Hz apart (or 3 Hz at a 2nd harmonic of
  174 Hz). A 1 s Hann frame has ~1.4–2 Hz resolution, so the peak wanders or switches between them: a
  frequency step of about the detune. Scaling from F1 (3 % → 240 m), a 1 % step is ~80 m. Run C event 1 had a
  0.93 % twin plus a turn. Experiment: `twin_detune_frac: [0, 0]` (stream-preserving).
- Ground reflection with mic heights up to 10 m gives a path difference 2h·sinθ up to 20 m, i.e. notches every
  17 Hz sweeping through the tones as elevation changes. Direct and image paths differ in Doppler by
  f·(2h/c)·d(sinθ)/dt ≈ 0.3 Hz at h = 10 m, unresolved in 1 s frames, so near deep notches (R 0.8–1.0) the
  spectral peak is biased and the ridge breaks. Note that real ground at grazing incidence has R → −1 with
  phase, not the synth's real positive 0.8–1.0. Experiment: `ground_reflection: false` alone on A/C/G.

### F15. truth.json does not record the hidden per-event source and kinematic draws. **Minor (diagnosability).** VERIFIED.
RPM ramp, twin detune, directivity, reflection coefficient, accel_frac, climb and turn_half are not in
truth.json (synth.make_scenario / realism.draw_source). I had to rebuild the scenario to see that run C's
event 2 had a −8 % ramp. Every ablation would be far cheaper with these recorded.

---

## 2. The realism gap (rank 5–39, error 100–410 m): explanations and how to tell them apart

Run C seed 0, cost surface with the true assignment (`diag.py`, 40 m grid ±600 m, then 8 m refinement, Δt
profiled; truth Δt = +2.01 s):

| inputs | cost minimum vs truth | Δt at min | cost min / at truth (per point) | rms@min / rms@truth (Hz) | missing pts at truth |
|---|---|---|---|---|---|
| base (all realism, no ramp) | 211 m | +0.66 | 1.809 / 2.667 | [0.58 0.66] / [0.92 0.75] | [5 0] |
| clean ADS-B | 195 m | +0.61 | 0.721 / 1.177 | [0.49 0.34] / [0.51 0.44] | [0 0] |
| clean ADS-B + true c | 238 m (other side) | +3.11 | 0.685 / 0.937 | [0.48 0.33] / [0.64 0.38] | [0 0] |
| clean ADS-B + true c + true mic h | 266 m (other side) | +3.31 | 0.684 / 0.985 | [0.48 0.33] / [0.64 0.39] | [0 0] |
| degraded ADS-B + true c + true h | 201 m | +0.61 | 1.829 / 2.638 | [0.60 0.67] / [1.02 0.73] | [5 0] |
| base + Newton-seed fix (F4) | 211 m | +0.66 | 1.447 / 2.077 | [0.57 0.66] / [0.91 0.75] | [0 0] |
| base + fix + event-1 σ 0.83→0.25 Hz (F3) | 197 m | +0.66 | 2.432 / 4.215 | [0.56 0.67] / [0.80 0.77] | [0 0] |
| clean + true c + true h + event-1 σ 0.25 | 249 m | +3.16 | 1.466 / 2.214 | [0.48 0.34] / [0.61 0.41] | [0 0] |

Each row's "event" columns are [event 1 (f8829a, turning, 1.6 km), event 2 (d8b927, overhead)].

Reading: the surface has a long (position, Δt) valley through the truth, with minima ~200–270 m away on
*either side* (Δt +0.6 s or +3.1 s against +2.0 s). Which side wins flips with sub-percent inputs (c by
2.4 m/s). Removing every ADS-B, c and mic-height error still leaves a ~200 m error. Up-weighting event 1
barely helps.

The valley geometry: the base minimum's displacement (−184, −104) m shifts event 2's along-track CPA by
−1.6 s, which matches the Δt shift of −1.35 s. Event 1 is the only breaker, and it is weak: 1.6 km away,
turning, and clipped by the clip start (1.0 τ before CPA; fit σ_t0 0.49 s). This 60 s, two-edge-clipped
scenario simply has a ~±200 m uncertainty along that valley, and audio-side systematic errors (wind, still in
the clean counterfactual; RPM walk; twin detune; reflection; and, SUSPECTED, the 60 Hz-hum harmonics at 180 Hz and 120 Hz that event 1's and event 2's
ridges sweep across, within the ±3 Hz guide band)
pick a point in it.

Ranked explanations:
1. **Weak observability × small systematic errors (F2; F3 secondary).** Geometry (same-direction corridors,
   near-overhead events, edge-clipped or distant events) leaves a near-degenerate (position, Δt) valley.
   Individually small systematic errors, each giving rank 1 alone (tens of metres in good geometry), are then
   multiplied into hundreds. This is the explanation that fits "each group alone fine, all together bad"
   *multiplicatively*. In run C seed 0 it is the dominant cause (table above). Distinguish: compute the
   Hessian eigen-spectrum at rank 1 per run and plot error vs (smallest eigenvalue)^(−1/2); rerun with
   `corridor_frac: 0`.
2. **Source-frequency changes (F1, F14).** These are the largest single biases (240–700 m for 3–10 % ramps
   near CPA). They are present in roughly 30 % of events × the probability that the ramp overlaps the pass
   (~20 % in 600 s clips, ~100 % in 60 s clips). Distinguish: stream-preserving ablation `rpm_ramp_frac:
   [0,0]`, `twin_detune_frac: [0,0]`, `rpm_walk_frac: 0`. In run C seed 0, removing the ramp cut the score 5×
   but moved the error only 261 → 232 m.
3. **Unmodelable-point artefacts (F4, F5).** Gaps over 20 s are common under realism. Each segment end inside
   an event falsely penalises the last r/c seconds. In run C seed 0 this inflated costs by 0.36–0.59 per point
   without moving the minimum, so it is a candidate for *rank* changes between hypotheses with different
   track coverage, not a location bias. Distinguish: apply the clamped seed (`mrfix.py`) inside a full
   `locate` on A/C/G and compare ranks; also set `adsb_gap_rate_per_min: 0`.
4. **ADS-B timing/altitude errors in degenerate geometry (F2).** Latency ±0.6 s and baro offsets of 60 m give
   ~50–75 m in crossing geometry and ~400–500 m in same-direction corridors. Distinguish: `adsb_latency_s: 0`
   and `adsb_baro_only_frac_prop: 0` with corridor on vs off.
5. **The metric.** With in-cell refinement and elongated valleys, "rank of the true cell" counts cells whose
   footprint touches the sublevel set through the truth. Rank 5–39 with 100–410 m error may describe one
   consistent ~0.5–1.5 km² credible region rather than 38 distinct wrong answers. Distinguish: report rank-1
   error and Δcost(truth − rank 1) in calibrated units alongside rank.

---

## 3. Checked and found sound (do not change without reason)

- physics.py: straight-pass model, exact received-time inversion, asymptotes, max rate, inversions. Reference
  check reproduced: 161.198 / 113.199 Hz, 6.980 Hz/s, and the inversions round-trip exactly. General model
  ratio = c/(c + rel·V/r) with emission-time radial velocity; sign and retarded-time Newton correct; Newton
  converges (|ṙ|/c < 1).
- Locked scoring design: one Δt for all events, per-event f0 profiled. Because both Doppler and the f0 scale
  are multiplicative, harmonic lock-on does not bias shape. Never reusing per-event t0 is right.
- In-cell refinement is necessary: the surfaces above show valleys hundreds of metres long within ~1 cell
  width.
- CLAUDE.md wind correction (v ≈ ground speed, ±0.4 m/s at 10 m/s wind) and the synth's first-order wind model
  (1e-4 ratio error).
- fit_ridge: robust multi-start plus correlation-inflated covariance is honest for correlated frame noise and
  for clipped passes, and the t_rx variance propagation is correct.
- Spectrogram times are segment centres (scipy), so prop (1 s) and jet (0.25 s) spectrograms have no
  differential time offset.
- NaN vertical rate → 0 (geo.velocity_enu_from_adsb); OpenSky `lastposupdate` used for position time.
- c mismatch (±3 m/s), wind (≤10 m/s), mic height (1–10 m), 1 kt speed quantization: each ≤ 45 m even in poor
  geometry. They are not where the realism gap comes from.

## 4. Open questions

1. How common are ≥ 2 % RPM changes during a pass in the real fleet near the benchmark site (fixed-pitch GA vs
   governed props/rotors vs jets)? This decides how much F1 matters. Measurable at milestone 4.5 from
   known-location recordings with ADS-B ground speed: compare the observed swing with the predicted plateau
   ratio.
2. OpenSky `geoaltitude`: is it HAE or MSL for the relevant fleet? DO-260B's GNSS-minus-baro difference field
   is installation-dependent. An MSL/HAE mix would be a 15–35 m height error in CONUS. Check against runway
   elevation for aircraft on short final.
3. Is the remaining ~200 m in run C after removing ADS-B, c and mic errors mostly twin-detune switching, RPM
   walk (0.3 %), ground reflection, or ridge selection on the turning event (F9)? Needs the stream-preserving
   ablations listed above on several seeds.
4. Does iterated ridge re-extraction guided by the assigned track's modeled curve (F9) recover the turning
   event's weight enough to break the valley?
5. What is a defensible effective-N and σ_model for the likelihood (F3, F6), so that silence nats, unmatched
   events and the ambiguity threshold can be put in one unit?
