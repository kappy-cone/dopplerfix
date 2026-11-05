# Skeptic review: cascade, design variants, validation methodology, real-data and US-scale readiness

Reviewer focus: cascade (steps 4–6), the two design variants, validation methodology, real-data and
US-wide readiness, automation vs human-in-the-loop, and confidence reporting. Physics, fitting and the score
function belong to another reviewer; I cover them only where they decide cascade or ranking outcomes.
I did not open `doppler.txt` or any other file under `reports/`.

Repo state reviewed: `ea8fe0d` (master). Fast tests: `104 passed, 22 deselected` (5 min 14 s on this machine,
run alongside the experiments below).

Evidence labels: **VERIFIED** (I ran an experiment), **CODE-READ** (follows from the code as written),
**SUSPECTED** (domain knowledge or inference that I have not tested here).

Scratch experiments are in
`<scratch>/skeptic_system/`:
- `e1_density.py` (+ `e1_out.txt`, `e1b.py`): how many wrong cells survive as traffic density rises. Truth-derived
  events with exact ridges from `physics.predicted_ratio_at`, straight tracks, flat ground, 10 km grid radius,
  K background props within 15 km. Both variants, with and without event 2's aircraft in ADS-B.
- `e2_cap.py`: what `max_hypotheses_per_cell` truncation drops.
- `e345.py`: E3 silence explainer leak; E4 ADS-B gap at CPA; E5 slow audible aircraft.
- `e6_tilekill.py`: tile pruning when an event has no compatible track.
- `driver.sh`, `summarize.py`, `count_hyp.py`: end-to-end `synth` (all realism on) → `locate --no-plots`, both
  variants, with outputs written to scratch through a copied config (`outputs.root` points to scratch).

---

## 1. Ranked findings

### F1 (critical). Lexicographic ranking plus the ambiguity rule give confident wrong answers when one event's aircraft is missing from ADS-B
**Claim.** `CellScore.rank_key = (n_unmatched, cost)` (`score.py:62–65`) ranks any cell that explains every
event above any cell that leaves one unmatched, however good the second cell's fit is. `ambiguity()` builds the
tied set only from candidates with the same `n_unmatched` as rank 1 (`score.py:356`,
`(sc <= sc[0]*(1+rel)) & (nu == nu[0])`). So when the true cell has one unmatched event, it can never be
"tied" with a wrong full-match rank 1. Nothing checks absolute evidence (rank-1 residual against the ridge
noise, or how much the full match beats the partial one).

**Evidence (VERIFIED, E1, robust variant, event-2 aircraft removed from ADS-B):**

| K background props (15 km radius) | seeds | rank 1 wrong (2 background tracks) | rank-1 error | status of the wrong rank 1 | true-cell rank |
|---|---|---|---|---|---|
| 10 | 1 | 0/1 | (rank 1 = a single-event locus cell 1.2 km away) | ambiguous | 5 |
| 30 | 3 | 1/3 | 5.5 km | ambiguous | 28 |
| 60 | 3 | 2/3 | 9.1, 8.3 km | **unique**, ambiguous | 113, 16 |
| 120 | 3 | 3/3 | 9.0, 7.0, 8.1 km | **unique, unique**, ambiguous | 178, 51, 152 |
| 240 | 3 | 3/3 | 7.7, 9.4, 7.5 km | ambiguous, ambiguous, **unique** | 310, 107, 103 |

For K ≥ 60, 8 of 9 runs return a wrong location 7–9 km away, and 4 of those 8 are labelled "unique". Where no
coincidence exists, the rank 1 is a single-matched cell and the status is "ambiguous", as it should be. With
ADS-B intact, both variants ranked the true cell 1st in 11 of 13 runs and 2nd in the other 2 (neighbouring
cell, 150–172 m). Scoring is therefore not the problem in these runs; the ranking rule is.

For scale (SUSPECTED): K=30 within 15 km is ~0.04 low-altitude aircraft per km², roughly a busy GA afternoon;
K=120–240 is terminal-area density. The search area here is only a 10 km disc. US-wide, the number of chance
two-track coincidences grows with area, so a lexicographic full match somewhere in CONUS is close to certain
whenever one real event has no track.

**Failure scenario.** A GA prop with only UAT ADS-B (978 MHz, which OpenSky's 1090 MHz network mostly does not
receive), a Mode-C-only aircraft, an ADS-B gap at CPA (F5), or a barometric-altitude error beyond tolerance
(F11). Any of these leaves one real event without a track. Near an airport, two unrelated tracks somewhere in
the region match both events by chance, and the system reports a location 5–10 km away, sometimes flagged
"unique".

**Severity** critical. **Confidence** high.
**Cheapest test.** Rerun `e1_density.py` with (a) ranking by total evidence, where an unmatched event costs a
fixed −ln p_unmatched in nats on the same scale as the ridge log-likelihood instead of ranking lexicographically,
and (b) a tied set built across `n_unmatched` levels.

### F2 (critical). Realistic end-to-end runs fail with confident status, and the failures come from the cascade, not only from scoring
**Evidence (VERIFIED, `driver.sh`, all realism on, `--cascade-decoys` default 3, 60 s clip, events at 15 s and
45 s, seeds 0–7).** The table is in §1a below. Robust: "correct" (rank 1 and "unique") in 3/8, confidently wrong by 3–5 km in 3/8.
Strict: the true cell survived in 0/8. Several failures are flagged "unique" while the true cell is not in the
tied set (C0 strict: a single survivor
1.15 km away; C1 both variants: 4.9 km away, both events assigned to two *decoy* tracks with rms 0.63 and
0.30 Hz, better than the true cell's fit).

Mechanisms seen:
- **Fit bias beyond tolerance.**
  - C1 event 2: fitted d = 478 ± 66 m against truth 1097 m (9σ, 56% short). Even with w_cov 2 and the 15 m/s
    wind term, the d tolerance is 568 m against an error of 619 m (z_d = −1.09), so the strict variant drops
    the true cell at CPA.
  - C6 event 1: d 391 m against truth 1301 m (−70%). C4 event 2: v 65.8 against 89.1 m/s (27σ).
  - Across 16 truth events, 11 have |Δd| > 3σ and 9 have |Δv| > 3σ. The fit covariance does not describe the
    error under realism (F10).
- **Hard silence on inaudible aircraft.** At C0's true cell, the strict variant's hard silence rejected the
  cell because of a near-traffic prop at 1869 m slant whose peak band SNR was −23 dB (inaudible) and a jet at
  5582 m (−25.6 dB). The slant-only audibility rule is physically wrong at ±6 dB level scatter, as HANDOFF says.
- **Tolerance blow-up in 60 s clips.** C1's tolerances are v ±30.6 / ±22.5 m/s, d ±1034 / ±568 m,
  t ±6.6 / ±7.6 s (edge-clipped passes, so w_cov is 2–4). CPA survivors are 1325 of 2821 cells: the cascade
  barely prunes the benchmark's own clip format.

**Severity** critical: this is the closest thing to the benchmark in the repo. **Confidence** high for these
seeds; the statistics are small (§3). **Cheapest next step.** Log, for every event matched to truth, (fit
error)/σ and (fit error)/tolerance for v, d and t_rx over ≥ 50 realistic seeds. If |z_d| > 3 happens in more
than about 1% of events, a hard d gate at the CPA stage is unsafe, whatever n_sigma is.

### F3 (major). Tile pruning defeats `max_unmatched_events`
**Claim.** `prepare_cascade` (`cascade.py:640–648`) marks a tile active only if *every* event's KD-tree has
samples in reach (`active &= ...`), and switches off *all* tiles if any event has no compatible samples at all
(`if ix.tree is None: active[:] = False`). It ignores `max_unmatched_events`.

**Evidence (VERIFIED, E6).** Robust variant, a real prop event plus an event labelled jet with no jet or unknown
track in the window gives `tiles run=0, survivors 0/0/0`. With both events prop, 95 cells survive.

**Failure scenario.** A spurious tonal event classified jet (or a real jet with no ADS-B) when no jet or
unknown-type track is in the window: the robust variant returns nothing, which is exactly the case it was built
for. At US scale (tile reach = radius + 18 km half-diagonal + 5 km), the true tile is also pruned when the
unmatched event has no compatible track within ~25 km.
**Severity** major. **Confidence** high. **Fix to test:** require reach for at least `required_matched`
events per tile, not all.

### F4 (major). The silence explanation leaks: any CPA-consistent pass explains itself, whichever hypothesis is chosen
**Claim.** `run_tile` passes every surviving pass of the cell as an explainer (`cascade.py:681–682`), and
`silence_filter` (`cpa` rule, `cascade.py:567–576`) marks an audible pass explained if *its* track passed the
CPA prefilter for any event at that cell. Each event can be produced by only one aircraft, so two aircraft
that are both consistent with event 1 cannot both be explained. Silence is also evaluated once per cell at the
Δt of the lowest *interim-cost* hypothesis (`cascade.py:666–669`), not per scored hypothesis. The penalty is a
per-cell constant added after the within-cell hypothesis choice (`score.py:336–339`).

**Evidence (VERIFIED, E3, strict).** Wrong cell W, 6 km from the mic: aircraft X and B both pass W at the time
of event 1 (slant ≈ 781 m and ≈ 820 m); Y matches event 2. Result: `n_audible 3, n_unexplained 0, silence
True`, hypotheses `[(X,Y),(B,Y)]`. The correct answer is one unexplained audible pass whichever assignment is
chosen.

**Failure scenario.** Parallel traffic in corridors, approach streams, or formation and pattern work near
airports. The decisive silence evidence against wrong cells disappears exactly where traffic is dense. In the
other direction, a correct per-hypothesis rule would also remove the accidental protection the true cell now
gets when `dedupe_same_aircraft` merges two real aircraft into one event (F12).
**Severity** major. **Confidence** high.
**Cheapest test.** Run silence per hypothesis, with explainers equal to that hypothesis's assigned tracks, and
rerun E1 and the sweep.

### F5 (major). Silence and CPA punish the same ADS-B defect twice; audible aircraft that cannot produce an event are counted as missing events
**Evidence (VERIFIED, E4/E5).**
- ADS-B gap 15–40 s (25 s, longer than `tracks.max_gap_s` = 20 s) spanning the CPA of a true event track.
  `clean_track` splits the track (`tracks.py:211`). Each segment's "CPA" is its endpoint, so the CPA stage
  fails. Strict: the true cell is eliminated. Robust: event 1 is unmatched **and** the two segments of the same
  aircraft are counted as `n_unexplained = 2` audible passes at the true cell.
- Helicopter at 8 m/s, 150 m AGL, 1.2 km from the mic. Its Doppler event, if detected, is discarded by
  `classify.min_aircraft_speed_mps` = 25 (`fit.py:338`), so its pass can never be explained. Strict: the true
  cell is rejected. Robust: a 3.77-nat penalty. At 500 m, robust hard-rejects the true cell.

**Rates.** With the realism layer's own gap model (Poisson 0.2/min, U(5, 60) s; events are *not* protected
from gaps, only from drops, `realism.py:285`), the chance that a gap longer than 20 s covers a given CPA is
about 0.2/60 × 0.73 × 40 s ≈ 10% per event, or about 19% that at least one of two events loses its track.
CODE-READ arithmetic.

The same double count applies to any audible aircraft whose event was missed: masked by a louder simultaneous
pass, beyond `ridge.max_ridges`, or merged by dedupe.
**Severity** major. **Confidence** high.
**Fix to test.** (a) Treat a track whose CPA lies in a gap (or within `max_gap_s` of a segment end) as
"undetermined" for both CPA and silence, not as failed. (b) Exclude tracks below `min_aircraft_speed_mps`, and
tracks in gaps, from silence.

### F6 (major). Hypothesis enumeration: cap truncates by enumeration order, partial hypotheses use up the budget, and the work explodes with poorly constrained events
**Evidence.**
- VERIFIED (E2). One cell, all passes consistent, the true assignment uses the last pass of each event.
  Robust (`max_unmatched_events` 1): N=2 with m=8 passes per event, N=3 with m=4, and N=4 with m=3 all drop the
  true assignment. The 64 kept include 14–36 partial hypotheses. Strict with N=3, m=6 also drops it. Order comes
  from `dict` insertion and `itertools.product` (`cascade.py:454–484`), not from interim cost.
- VERIFIED (sweep C2). The detector returned 6 events for a 2-event scenario (extra real near-traffic aircraft
  with σ_d up to 1741 m, so near-unbounded tolerances). Robust variant: all 117 silence survivors hit the
  64-hypothesis cap (7488 hypotheses). The cascade took 80 s in the Python enumeration loop and scoring took
  416 s, **501 s for a 60 s clip in a 6 km region**. Strict: 87 s of enumeration for 0 survivors. The
  `product` loop iterates over every combination even after it rejects them; the cap only stops *accepted*
  ones.
- Also CODE-READ: `required_matched = max(1, N − max_unmatched)` (`cascade.py:402–403`). With 6 events the
  robust variant still needs 5 matched, so it tolerates one spurious event, not "spurious events" in general.

**Severity** major (critical for window/relative time modes, where m per cell-event grows with the window).
**Fix to test.** Branch-and-bound over events in order of interim |z|, keeping the best-k by cost. Cap partial
hypotheses separately. Drop or down-weight events whose σ_d/d > 0.5 before the cascade.

### F7 (major). The robust variant turns the cascade into a locus generator; at US scale it will not prune
**Evidence.** VERIFIED (E1): robust offset survivors (10 km grid radius, 7.8k cells) were 333–727 cells at
K=30, 922–2770 at K=60, 2119–3052 at K=120 and 3080–4079 at K=240, against strict 14–430. The robust survivors
are mostly single-event loci, about 10–25 cells per compatible track. Probabilistic silence removes 2–38%
(e.g. 3052 → 2730 at K=120, 4079 → 2523 at K=240). Hard silence on the strict side removes 0–97%, and more
than 50% in every run with K ≥ 120 (154 → 28, 430 → 15). CODE-READ (`cascade.py:589`): hard rejection needs
P(audible) ≥ 0.99, i.e. r ≤ 0.20 × threshold (600 m prop, 1200 m jet).

**US-wide extrapolation (SUSPECTED).** With thousands of compatible props airborne across CONUS, two events
produce ~10⁵ single-event-locus cells, each with ≥ 2 hypotheses. The sweep measured 33–56 ms per hypothesis
including refinement (C1 robust: 2115 hypotheses in 69 s; C2 robust: 7488 in 416 s), so this is hours of
scoring. `refine_max_cells` = 500 then ranks
refined cells against unrefined centre scores (`score.py:332`). Refinement only lowers a score, so a true cell
outside the top 500 by centre score is handicapped.

Also, the 1-minute benchmark clip gives edge-clipped passes with w_cov 2–4 (F2), which inflates every
tolerance.
**Severity** major. **Confidence** medium (extrapolation).
**Cheapest test.** Run the US-wide mode on a synthetic CONUS TrackSet built from a real OpenSky hour, with
truth events injected, and report survivors and run time per stage for both variants.

### F8 (major, SUSPECTED, domain). The prop-event ADS-B coverage assumption is untested and probably the dominant real-data failure
**Claim.** In the US, many GA props meet the ADS-B mandate with UAT (978 MHz). OpenSky (1090 MHz) mostly does
not see them unless ADS-R or TIS-B rebroadcasts them. Below ~1000 ft AGL, receiver line-of-sight gaps are
common. The synthetic world never tests this: `adsb_drop_prop_frac: 0.0` (`config.yaml:274`), and events are
explicitly protected from drops (`realism.py:285`, `protect=`). Together with F1 and F5, a missing event track
is the condition that produces confident wrong answers (robust) or zero survivors (strict).
**Severity** major. **Confidence** medium.
**Cheapest test.** For one day near KBJC/KBDU, compare FAA registry aircraft seen in FlightAware or ADSBx (which
take UAT) with those present in OpenSky state vectors. Then rerun the sweep with `adsb_drop_prop_frac` set to
that measured rate, without event protection.

### F9 (major). Synthetic validation is largely circular, and the strict variant's apparent fail-safety is partly an artefact of it
CODE-READ, with one VERIFIED consequence:
1. **Silence consistency by construction.** Every non-event is placed ≥ `inaudible_margin` (1.2) × the
   pipeline's own `audible_slant_m` from the mic (`synth.py:378,385`; `place_target_decoys` likewise). The
   true cell can only fail silence through the realism layer's `n_near_traffic` = 4. E1 copies this
   construction (background ≥ 3.6 km from the mic). At K=120–240 in a 15 km radius, a real 3 km circle would
   contain about 5–10 aircraft at once. Strict's good E1 results with ADS-B intact (true cell rank 1–2, wrong
   cells removed by hard silence) therefore depend on the generator keeping the true cell silence-clean. In the
   realistic sweep, which adds 4 near-traffic aircraft at 0.5–1.5× threshold, hard silence rejected the true
   cell in 4/8 seeds (VERIFIED, §1a).
2. **Decoys come from the pipeline's own survivors.** `cmd_synth` runs the pipeline's CPA+offset stages
   (`offset_survivors_enu`) and places decoys over those cells (`cli.py:299–315`). This tests that silence
   rejects cells the pipeline itself lets through, using the thresholds it itself uses. CLAUDE.md admits the
   self-consistency.
3. **Scoring tests share the forward model.** `test_true_cell_ranks_first_after_locked_scoring` builds ridges
   from `physics.predicted_ratio_at` at the true mic with the scorer's c, clean tracks, white noise of 0.1 Hz,
   and a 0.05 s σ_t covariance. It proves the implementation is correct, not that the method is valid.
4. **Flat world.** Every scenario uses `FlatDEM`. No DEM, geoid, slope or nodata path is exercised end to end;
   DEM-error sensitivity has never been run (HANDOFF).
5. **Traffic.** About 20–25 aircraft in straight lines across a 12–18 km disc. Corridor alignment applies to
   *events* only (`perturb_kinematics`, `realism.py:105–127`). No approach streams, patterns, holds or
   formation. When every event flies the same line (one approach path), the mic's mirror image across that line
   is an exact Doppler alias for all events, and only silence can separate the two; this case is never
   generated. Search area is 2821 cells against ~2×10⁸ US-wide.
6. **Spurious sources are mostly filtered by construction.** Synthetic cars move at U(10, 30) m/s
   (`realism.py:233`), mostly below the 25 m/s `min_aircraft_speed_mps`. Highway traffic (30–40 m/s), trains
   (30–80 m/s, strong tonal Doppler), motorcycles and drones are absent.
7. **Truth events always have ADS-B**, and HANDOFF's ablation used one seed per scenario per group.
**Severity** major. **Confidence** high.

### F10 (major). Covariance-based tolerances are not calibrated under realism; the "wind" sys term is silently absorbing fit bias, and still not enough
**Evidence.**
- VERIFIED (sweep census, §1a). 11/16 truth events have a d error > 3σ of the fit covariance, and 9/16 a v
  error > 3σ (up to 27σ). The `n_sigma · w_cov · σ` part of `event_tolerances` (`cascade.py:135–156`) is
  therefore not a statistical bound.
- What keeps true cells alive is `wind_sys_mps` = 15. CLAUDE.md's corrected physics says wind changes the
  Doppler v by only about v·w/c ≈ 2 m/s, so that term is really absorbing fit bias. Through d·w/v (≈ 200–360 m
  at d = 1–1.6 km) it does the same for d.
- It is still not enough: C4 (Δv = −23 m/s), C1 (Δd = −56%) and C6 (Δd = −70%) failed the gate.
- By contrast, t_rx errors (≤ 1.5 s, 5/16 > 3σ) never exceeded 0.47 of the t_rx tolerance:
  `timing_sys_s` plus the grid term are adequate there.
- At the same time, tol_v ≥ 15 m/s makes v nearly useless for separating GA props (45–90 m/s). That raises the
  coincidence rate (F1) and hypothesis counts (F6).
- The grid term is added linearly (h = 141 m on d; h/v + h/c on t). That is correct as a hard bound; keep it.

**Consequence.** "Tightening wind_sys to the physical value" (the HANDOFF open decision) would, with today's
fit, eliminate true cells much more often. Leaving it as is keeps v tolerance loose while d and v bias still
kill some true cells.
**Cheapest test.** Over ≥ 50 realistic seeds, take the empirical quantiles of (fit − truth) for v, d and t_rx,
per realism group. Set the sys terms from those quantiles, under honest names ("fit model error", not "wind").
Consider moving d from a hard gate to a soft term, because d is the least robust parameter (d = f0 v²/(c·maxRoC)
depends on the curvature at CPA).

### F11 (minor–major). Barometric altitude is treated as ellipsoidal height with a 150 m allowance on d only
CODE-READ. `tracks_from_frame` chooses baro per aircraft when < 50% of fixes are geometric (`tracks.py:146–150`)
and stores it as `alt_m`, with no geoid term and no QNH or temperature correction. `baro_alt_tol_m` is added
only to the d tolerance (`Tolerances.d_for`), not to scoring or silence. Pressure altitude differs from true
height by ~8 m/hPa of QNH deviation (±20 hPa → ±160 m) plus ~4% per 10 °C ISA deviation of height above the
datum. At Denver in summer, +150 to +300 m is plausible (SUSPECTED). The realism layer models baro offset as
N(0, 60 m) (`realism.py`), which is optimistic. Also, OpenSky geoaltitude may be HAE or MSL depending on the
avionics; at Boulder the difference is 16 m (open question).

### F12 (minor). `dedupe_same_aircraft` merges distinct aircraft easily
VERIFIED. `_simple_ratio(r, 0.03, max_den=4)` (`fit.py:356–363`) accepts 46% of random f0 ratios for two props
drawn from 80–180 Hz. Two different aircraft with CPAs within 3 s, v within 15% and d within 35% are merged
about half the time. One aircraft then becomes an audible pass with no event. This is masked today only by the
F4 leak.

### F13 (minor). Time modes beyond `known` are not usable yet
CODE-READ.
- `relative` mode loads the whole track file (`cli.py:479–480`, `win = None`). `cascade_inputs` builds a dense
  TrackSet over the full span.
- A day-long `window` builds K × 345,600 samples × (xyz 3 + vel 3 + h + gs) × 8 B. For 2000 regional segments
  that is about 44 GB.
- The event KD-trees and silence index cover the whole window.
- With a window of hours, the offset filter only checks inter-event offsets. Approach streams of identical
  aircraft become exact aliases along the corridor, and m per cell-event grows, which makes F6 binding.
- Two passes of one track within the window are handled correctly (separate runs in `_runs`), which I checked.

### F14 (minor). DEM and terrain handling are untested and lose cells silently
CODE-READ. `build_tile` drops cells whose DEM sample is NaN (`geo.py:322–323`) without counting them, so a
true cell on a DEM void or edge disappears from every stage count. A nodata value the DEM does not declare
(e.g. SRTM −32767 with nodata −32768) would pass through as ground height. Refinement keeps the cell-centre
DEM height (HANDOFF), and the cascade has no terrain-slope term; 30% slopes in the Boulder foothills give ±40 m
across a cell. Terrain shielding and refraction (shadow zones) make audibility anisotropic, but the silence
thresholds are isotropic.

### F15 (minor). The run.json automation record is incomplete, and some interfaces will need rework for HITL
CODE-READ. `manual_inputs()` (`cli.py:420–425`) checks only `detect.manual_windows`. A run with per-clip edited
tolerances, an edited time prior, or a hand-drawn `--region` is still recorded as `human_in_loop: false`. The
config snapshot is stored, but there is no hash or diff against the committed config. For milestone 9:
- `locate` cannot consume a saved event list (`cmd_events --out` writes one, but `locate` always re-detects).
- There is no hook to exclude tracks.
- PassTable and hypotheses are not persisted.
- `cmd_locate` is a ~380-line orchestrator.

The stage functions themselves (`cascade.*`, `score.rank_cells`) are pure and separable, which is good.
Analyst decisions could be applied by filtering `fits`, `prepared` and the region before `cascade_inputs`;
that needs entry points, not a redesign.

### F16 (minor). Small inconsistencies
- `CascadeParams.audible_slant_m` default has `unknown: 6000` (`cascade.py:54`) and `SynthParams` too
  (`synth.py:103`), against config 3000. Any caller using defaults gets the opposite leniency. CODE-READ.
- The silence penalty is normalised by all ridge points, including unmatched events (`score.py:323–324`),
  while `cost` is normalised by matched points only (`score.py:194`).
- The tie threshold `score ≤ best·(1+0.15)` is relative to a mean loss, so its meaning in nats changes with
  the level of the best score. In C0 robust (best 10.1, ~200 points), 15% is about 300 nats; in C1 (best 1.34),
  about 40 nats. It is not a statistical criterion.

### 1a. Realistic end-to-end sweep (VERIFIED; all realism on, 60 s, events 15 s and 45 s)
Command per seed:
`cli.py synth --seed s --duration-s 60 --event-times 15,45` (default realism, 3 cascade decoys), then
`locate --no-plots` with each variant. Region `synth_boulder`, 2821 cells. "dd" is the fitted d minus the truth
for the worse of the two truth events; "z" is that error over the event's full d (or v) tolerance.

| seed | events detected | robust: true cell / rank / rank-1 error / status | strict: true cell / rank-1 error / status | worst fit error | robust run time |
|---|---|---|---|---|---|
| C0 | 2 | survives, rank 5, 261 m, unique (true not tied) | rejected by silence, 1 survivor 1152 m, **unique** | dd −543 m (z_d −0.74) | 68 s |
| C1 | 2 | rank 313, **4885 m, unique** (both events → decoys) | rejected at CPA, **4885 m, unique** | dd −619 m (z_d −1.09) | 74 s |
| C2 | 6 | rank 2, 70 m, unique (tied) | 0 survivors | dd −166 m | **502 s** (7488 hyps) |
| C3 | 2 | **rank 1, 102 m, unique** | rejected by silence; rank 1 is the neighbour cell, 29 m | dd −134 m | 69 s |
| C4 | 4 | rejected at CPA (z_v −1.47), **4759 m, unique** | 0 survivors | dd +359 m, z_v −1.47 | 12 s |
| C5 | 2 | **rank 1, 43 m, unique** | rejected by silence, 0 survivors | dd −160 m | 25 s |
| C6 | 4 | rank 57, **3032 m, unique** | rejected at CPA, 0 survivors | dd −910 m (z_d −1.19) | **432 s** (14,606 hyps) |
| C7 | 2 | **rank 1, 2 m, unique** | rejected by silence, 0 survivors | dd −33 m | 58 s |

Summary:
- **Robust.** "Correct" (rank 1 and unique) 3/8. Rank-1 error ≤ 261 m in 5/8. **Confidently wrong by
  3.0–4.9 km in 3/8**, every one labelled "unique". The status was "unique" in all 8 runs: the ambiguity
  measure never abstained.
- **Strict.** The true cell survived all stages in **0/8**: silence rejected it 4×, CPA 3×, and 1 run (C2) had
  0 survivors. 5/8 returned no answer. 3/8 returned "unique" answers, of which 1 was right (29 m, a neighbour
  cell) and 2 wrong (1.15 km, 4.9 km).
- **Fit-error census (16 truth events).** d errors range from −70% to +41%. **11/16 events have |Δd| > 3σ**
  of the fit's own covariance (up to 9.5σ), and **9/16 have |Δv| > 3σ** (up to 27σ; C4 v 89.1 → 65.8 m/s).
  The cascade gate rejected 3 of these 16 events (C1 d, C4 v, C6 d at −70%).
- Robust run time for a 60 s clip in a 6 km region: 12–502 s, driven by hypothesis count.

I stopped the sweep after one 600 s run, **A0** (seed 0, events at 150 s and 420 s, HANDOFF's scenario A).
- The detector found 4 events:
  - the 2 truth events;
  - a **car-like source at v = 26.9 m/s, d = 138 m**, which passes the 25 m/s filter (F9.6);
  - a garbage event with v = 216 ± 524 m/s and d = 7441 ± 31,101 m, which matches almost anything.
- Robust: the true cell fails CPA (event 1 d 931 vs 1359 m, z_d −1.59). Rank 1 is 407 m away, using the right
  two tracks, plus the garbage event assigned to a twin (rms 3.5 Hz) and the car left unmatched. Status
  "unique". Cascade 162 s, total 214 s.
- Strict: 0 survivors, 178 s (157 s of enumeration for nothing).

Eight seeds are still few, but the HANDOFF single-seed numbers ("all realism: rank 5–39") understate how often
the output is *confidently wrong*: neither rank nor error distance shows that.

---

## 2. The two design variants and hybrids

**Strict** (all events matched, hard silence)
- *Fails at the true cell:*
  - whenever any event lacks a consistent track: UAT or no ADS-B, a gap at CPA (E4), fit bias beyond tolerance
    (C1), baro error, or a spurious event (C2: 6 events, 0 survivors);
  - whenever any aircraft inside the slant threshold was not detected, audible or not (C0: a −23 dB prop at
    1.9 km killed the true cell; E5: a slow helicopter).
- *Good property:* it often fails with zero survivors, an honest "no answer" (E1 K ≥ 60 without event-2
  ADS-B: 0 survivors). But the hard silence that removes the wrong cells is the same mechanism that removes the
  true cell under realistic near traffic (4/8 seeds). "No answer" is therefore the usual outcome, not just the
  fail-safe one (5/8 seeds).
- *Still fails confidently sometimes:* when the true cell dies but wrong cells live. C0 strict: 1 survivor,
  1.15 km away, "unique". C1: 4.9 km, "unique". E1 K=30: 13 wrong cells, flagged ambiguous.
- *Realistic sweep:* the true cell survived in 0/8 seeds (§1a). Strict as specified in CLAUDE.md steps 5–6
  cannot be the default for real data.

**Robust** (≤ 1 unmatched, lexicographic, probabilistic silence)
- Survives one spurious event, one missing track, and quiet near traffic.
- Pays for it three ways: (i) confident wrong answers through lexicographic ranking (F1); (ii) a cascade that
  does not prune (F7) plus hypothesis explosion (F6); (iii) silence that is almost inert (hard rejection only
  inside 0.2 × threshold).
- Its tolerance of unmatched events is undone by tile pruning (F3).
- *Realistic sweep:* right to within 261 m in 5/8 seeds, but confidently wrong by 3–5 km in 3/8, all marked
  "unique".

**Hybrids worth testing** (rank them on the same seeds, reporting both rank and error, and calibration):
1. *Strict first, robust fallback* (HANDOFF's suggestion). The trigger must not be "zero survivors" alone,
   because strict also returns wrong survivors (C0). Trigger the fallback when strict's rank 1 fails an
   absolute fit test (e.g. per-event rms > 1.5× ridge noise, or the locked Δt at a prior edge). Report both
   answers when they disagree.
2. *Evidence ranking instead of lexicographic.*
   - Score a hypothesis as Σ (ridge log-likelihood in nats, with σ calibrated for decimation and
     autocorrelation) + Σ_unmatched ln p_u + per-hypothesis silence log-probabilities.
   - Estimate p_u from the measured non-ADS-B and spurious rate (F8).
   - Report the evidence gap between the best full and the best partial hypothesis, across `n_unmatched`
     levels.
3. *Per-hypothesis silence with an "undetermined" class* for tracks in gaps, slow tracks and baro-only tracks
   (F4, F5). This keeps silence decisive against wrong cells without double-counting defects at the true cell.
4. *Hard-gate only what is measured robustly.* In the sweep, t_rx errors reached 1.5 s (5/16 > 3σ) but never
   exceeded 0.47 of the t_rx tolerance. v and d errors did exceed theirs (F10). Keep t_rx (and the shared-Δt
   offset filter) as hard gates. Gate v and d on empirical error quantiles, or move them into the score.

---

## 3. Validation gaps: what a convincing validation needs before the real benchmark

1. **Statistical power.**
   - HANDOFF's ablation is one seed per scenario per group. 3/3 successes still allow a true success rate as
     low as 37% (one-sided 95% Clopper–Pearson); 8/8 allows 69%.
   - Use ≥ 50 seeds per configuration and report success rates with intervals.
   - Pre-register the metrics and thresholds before looking at results, as CLAUDE.md asks.
2. **Metrics that measure confidence, not only rank.**
   - Precision of "unique" claims: the fraction of "unique" runs whose rank 1 is within X m of the truth. F1 and
     the sweep show it is far from 1.
   - Coverage of a reported credible region, and its area.
   - Abstention rate.
   - The full error distribution, not only "true cell rank 1": rank 2 at 70–260 m and rank 1 at 9 km are both
     "not correct", but they are very different failures.
3. **Break the circularity.**
   - Place non-events independently of the pipeline's audibility thresholds, and test silence with true
     acoustic audibility (synthesised level and the detector's actual response).
   - Generate decoys from a traffic model, not from pipeline survivors.
   - Evaluate the scorer on ridges extracted from realistic audio, not on model-generated ridges.
4. **Real traffic.**
   - Replay real OpenSky hours, near KBJC/KAPA and around a class-B airport, as the background TrackSet.
   - Inject synthetic events only for the hidden aircraft, keeping real corridors, patterns, density, gaps,
     baro-only fraction and type-DB gaps.
   - Measure the coincidence rate (E1 analogue) on that real traffic.
5. **Missing tracks.** Sweep `adsb_drop_prop_frac` (0, 0.3, 0.5) *including event aircraft*, with gaps over
   CPA. This is the most likely real-world condition.
6. **Terrain.** A real DEM (Boulder foothills), DEM bias and noise, geoid on and off, nodata patches, and mic
   heights from 1 to 10 m.
7. **Spurious sources** above 25 m/s: highway vehicles, trains, motorcycles, drones.
8. **Benchmark format.** 60 s clips with 1–3 edge-clipped events, in US-wide mode with real CONUS traffic for
   that minute. Report per-stage survivors, run time and memory. This has not been run at any scale.
9. **Fit-bias census.** z-scores of v, d and t_rx against truth across realistic seeds, per realism group,
   feeding the tolerance decision (F2/F10).
10. **Both variants side by side** on every item above, as HANDOFF requires, plus the hybrids in §2.
11. **Milestone 4.5 (real known-location recordings)** before any further threshold choices: audibility
    thresholds, level σ, `min_aircraft_speed_mps`, wind_sys and baro tolerance are all provisional and directly
    drive F1–F5.

---

## 4. What I checked and found sound (do not change without cause)

- **Offset filter.** The shared-Δt filter is correct and complete: ∩ over events of ∪ over passes, testing only
  pass left ends (any non-empty intersection contains one), vectorised, and the same for any N
  (`offset_filter`; unit tests; E2's enumeration finds every feasible combo when not capped). Interval bounds
  are clipped to the prior.
- **Locked Δt.** One Δt is shared by all events, both in the cascade and in scoring. Independent t0s from the
  characterisation fit are never used as scoring parameters.
- **CPA refinement.** It runs on the linearly interpolated polyline (s−1..s+1), consistent with
  `TrackSet.sample` (unit test). Separate passes of one track within a time window become separate runs
  (`_runs` breaks on non-adjacent samples), so multiple passes are handled.
- **Grid term added linearly** (δd ≤ h, δT ≤ h/v + h/c). This is a correct hard bound and should stay out of
  the quadrature.
- **Clock error in the Δt prior**, not in per-event tolerances.
- **Streaming and pruning.** Tile streaming with KD-tree pruning, tile-size invariance (test), no full grid in
  memory, and per-stage counts. US-wide grid counts degrade to area-based above 5000 tiles and say so
  (`grid_count_exact`).
- **Geometry.** ECEF throughout. Geoid via PROJ grids with an explicit fallback warning. DEM windowed per
  tile.
- **Distinct aircraft** are checked by icao24 across track segments (test). OpenSky `lastposupdate` is used as
  the position time.
- **Outputs.** run.json records `human_in_loop`, the config snapshot, the git commit and dirty flag, per-stage
  counts, tolerances per event, ambiguity, and disk footprint. Truth is used for evaluation and display only;
  I traced every use in `cmd_locate`.
- **Unknown type class** matches either event class and uses the lenient silence threshold (config). This is
  the right direction for type-DB gaps.
- **Storage.** zstd Parquet, COG / sparse tiled raster, shared `plotly.min.js`, `clean`.
- **Strict path still available**, as requested (`STRICT` in tests; both configs ran in my sweep).

---

## 5. Open questions

1. What fraction of prop events near the intended benchmark site will have an OpenSky 1090 track at all?
   (F8 decides whether the strict variant is ever usable on GA traffic, and how large p_u is.)
2. Where does the 30–56% d bias under realism come from: ground-reflection interference, half-order or
   harmonic lock, climbs, or RPM ramps? The other reviewer's area, but it sets the CPA gate.
3. Should "unique" mean anything without an absolute goodness-of-fit test? Today a wrong rank 1 with a good
   fit to biased measurements (C1: decoys, rms 0.3 Hz) is indistinguishable from a right one.
4. Is OpenSky `geoaltitude` HAE or MSL for the relevant avionics? (±16 m at Boulder, more elsewhere.)
5. For the benchmark, will the clock be trusted to ±10 s? Phone metadata (time zone, trimmed clips, file
   creation time) can be off by far more, and a misspecified Δt prior silently empties the CPA stage. There is
   no diagnostic that says "consistent with some other Δt".
6. Should the system abstain (report "no location") when the best full-match evidence does not beat the best
   partial match by a calibrated margin? That would make both variants safer than either is now.
