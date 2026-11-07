# Design comparison: the doppler.txt video pipeline vs ours

Session 4, analysis only. Nothing in code, config or CLAUDE.md was changed. The source is
`doppler.txt`, an auto-caption transcript (839 lines) of a video where someone builds a similar
tool. Line numbers are cited as `L123`. The transcript is third-party content, so it is no longer committed.
Keep a local copy at `data/doppler.txt` (gitignored); the line numbers refer to that file. The transcript only has the narration. Anything that was
only on screen (the silence thresholds, the type lookup table, their equations, their code) is
missing, so "not mentioned" below means "not in the narration", not necessarily "not done".

---

## 1. Their design, stage by stage

**Shown working** means we see it demonstrated in the video (a count, a plot, a result). **Claimed**
means asserted with no evidence in the narration.

| Stage | What they do | Evidence |
|---|---|---|
| Case | Security-camera video. Only the date (Sept 8) and "continental US" are known, so the time prior is a whole day (L14–16). | Shown: final match to Street View and the camera (L735–751) |
| Event detection | By eye: "zoom into the right frequency, and then increase the resolution" (L46–48). Three curves are seen and **two are chosen** ("these two are what we'll be using", L73–74). The faint first one is kept for a separate demo. | Shown (manual) |
| Ridge / curve extraction | None for the main result. The scorer reads spectrogram brightness directly. For the faint event: "I just kind of traced it with a line" (L677–681). | Shown (manual) |
| Per-flyover fit | They overlay an ideal straight-pass curve by hand and slide v, f0 and t0: "decent fit … at 100 m/s … pretty sure it wasn't going 70" (L283–286), "151.15 … 151.13" (L289–291). The output is **generous ranges** for v and d plus a CPA time with "a couple seconds" of error (L279–298). One side of the curve does not fit a straight pass (L274–278). | Shown (manual, no numbers given for the ranges) |
| Aircraft class | The audio is called a prop by ear (L105–110). Tracks: they take a type-code lookup and "filtered out all the known jet types", so unknown types are kept (L111–119). | Shown |
| Flight data | A Python scraping package, run "for a few hours" for all CONUS flights that day. Whole flight paths come back, including Hawaii (L88–99). "50 GB parquet files" (L58). No cleaning or interpolation is described. | Claimed / partly shown |
| Height | "Altitude is the height above sea level", plus a DEM per grid point, gives the 3D slant distance (L167–179). | Shown (but see §5: the premise is wrong) |
| Grid | 200 m points within 10 km of each flight ("pretty conservative", L146–150). | Shown |
| CPA and delayed CPA | For each point and flight: CPA time, then the "delayed CPA time" = CPA + slant/c (e.g. 22:46:34 → 22:46:46, L180–183). | Shown |
| Offset filter | Keep points where one flight's delayed CPA and another's are 920 s apart, "± wiggle room" of roughly 5 s (L197–218). 35,000 points survive in a small test area (L221). | Shown |
| Speed and distance filter | Flight segments are filtered by each event's v range. Candidate points are laid only at that event's slant-distance range from those segments. Then the 920 s test runs. About 73,000 points survive US-wide, mostly near airports (L299–326). | Shown |
| Silence filter | "Disqualified any point that would have heard a flight **between our two events**", with "pretty conservative" thresholds (L339–347). 70,000 → 15,000 (L363–365). | Shown (thresholds only on screen) |
| Full curve model | Emission-time radial velocity: the frequency heard at t comes from the plane's velocity when it emitted the sound heard at t. They stress that this is "not just the same curve delayed" (L472–506). | Shown (side-by-side plot described) |
| Scoring | "Sum the brightness values that each curve covers" (L520–523). Event 1: sweep a few seconds of time and the f0. Event 2: sweep f0 only, with its time locked to event 1 through the flight geometry ("entirely locked", L555–575). | Shown |
| Refinement | The 200 m grid is "a little bit too coarse … up to 141 m off" (L576–585). Survivors are re-gridded to 100 m (about 30–40 k points). After the flight pair is identified, a finer heat map is drawn per event and combined (L641–648). | Shown |
| Ranking | Per-event scores go on a scatter plot. "Group them by flight pair … take the highest-scoring point for each flight pair" (L606–613). Winner 20.48 vs runner-up ≈15. The runner-up's event 2 is badly off (L617–638). | Shown |
| Single-event demo | The faint 1-minute event "if you knew the time … 8:37 p.m. UTC". A US-wide search restricted to segments at that time, scored by distance to the hand-traced line, gives the same location (40.87, −73.5) ranked first (L652–694). | Shown, but see §5 |
| Countermeasures | High-pass then MP3, and "boost all frequencies and the curve is back" (L761–771). A JBL speaker playing a Doppler curve while he talks (L777–779). Phone call: the curve survives, but **disappears while he is on mute** (noise suppression) and returns when he talks (L795–807). Cuts defeat the offset (L781–783). | Demos only, n = 1 each |
| Not mentioned at all | Wind, the speed-of-sound value or range, mic height, ground reflection, RPM/power changes, harmonic choice, aircraft without ADS-B, spurious non-aircraft events, uncertainty or confidence, any quantitative validation beyond one case. | — |

Failure modes they hit: (1) the non-retarded model "would make our search fail entirely" (L472–474);
(2) survivors cluster at airports until silence is applied (L317–326); (3) 15,000 survivors are left
after every cascade filter, so curve scoring is essential (L366–381); (4) the 200 m grid is too coarse
for locked scoring (L576–585); (5) brightness scoring did not work on the faint curve, so they traced it
by hand (L675–681); (6) phone noise suppression gates the curve (L802–807).

---

## 2. Mapping to our implementation

| Item | Theirs | Ours | Verdict |
|---|---|---|---|
| Event detection | manual, zoomed spectrogram | `detect.py:detect_events_audio` (tonal statistic, theory-floored baseline, chunked) | **different** (ours automated) |
| Choosing which events to use | human picks 2 of 3 | every detection is used; `fit.py:dedupe_same_aircraft`, `is_harmonic_of`, `classify.min_aircraft_speed_mps`; `cascade.max_unmatched_events` | **different** (see §3.1) |
| Curve extraction | none (brightness), or hand-traced | `ridge.py:doppler_hough` → `guided_ridge`; peeling in `fit.py:characterize_window` | **different** |
| Per-event fit | hand overlay, generous ranges | `fit.py:fit_ridge` (soft-L1 multi-start, whitened stage-2 with f0-drift nuisance, covariance, correlation inflation) → `cascade.py:event_tolerances` | **different**, ours stronger |
| Prop/jet on tracks | drop known jets, keep unknowns | `aircraft_types.py:compatible` (unknown matches either) | **same** |
| Prop/jet on events | by ear | `classify.py:decide` (harmonic count + broadband rise) | **different** (ours automated, provisional thresholds) |
| Flight-data ingest | whole-day CONUS scrape, no cleaning described | `tracks.py:normalize_frame` (OpenSky `lastposupdate`), `clean_track`, `interpolate_track` (PCHIP in ECEF), `build_trackset` | cleaning **missing on theirs**; OpenSky bulk ingest **missing on ours** (milestone 7) |
| Altitude datum | "above sea level" + DEM | `geo.GeoidModel` (GEOID18/EGM96), geometric preferred, `cascade.baro_alt_tol_m` | **different**, ours correct |
| Grid and reach pruning | 200 m points ≤ 10 km from flights | `geo.build_tile`, `cascade.iter_cascade` (tiles out of reach skipped) | **same** idea |
| CPA and delayed CPA | per point per flight | `cascade.py:cpa_prefilter` + `refine_cpa` (`t_rx_pred = t_emit + r/c`) | **same** |
| Speed and distance filter | segments by v range, points at d range | `cascade.build_event_index` (KD-tree, radius d+tol, event time window) + `cpa_prefilter` (z_v, z_d) | **same** structure; tolerances **different** (eyeballed vs covariance ⊕ sys) |
| Offset filter | 920 s ± wiggle, 2 events | `cascade.py:offset_filter` (∩ events ∪ passes, shared Δt, N events) | **same** for N = 2; ours general |
| Time prior | date only (24 h); known-to-minute for the demo | `cascade.dt_prior_from` (known / window / relative) | **same** capability; our benchmark is known-time only |
| Silence | hard, only **between** the two events, conservative thresholds | `cascade.py:silence_filter`: whole clip, `silence_min_audible_s`, `silence_explain: cpa`, hard or probabilistic | **different** |
| Curve model | emission-time radial velocity | `score.py:modeled_ratio` (Newton on t_e + r/c = t_r) | **same** |
| Locked scoring | event-1 time + f0 sweep; event-2 f0 sweep, time locked | `score.py:score_hypothesis` (one Δt for all events, f0 profiled per event) | **same** principle |
| Cost function | sum of spectrogram brightness on the curve; distance to traced line for the faint event | pooled mean Huber of ridge residuals in σ units, `score._event_cost` | **different** (see §3.2, change #5) |
| Event combination | sum of per-event scores | pooled over points; `CellScore.rank_key` = (n_unmatched, cost) | **different** |
| Grouping | best point per **flight pair** | `score.best_per_cell` (best per cell) | per-assignment grouping **missing on ours** |
| Sub-grid precision | re-grid to 100 m; fine heat map per identified pair | `score.py:refine_in_cell` (L-BFGS-B over east, north, Δt) | **different**, ours better; fine cost map as a diagnostic **missing on ours** |
| Confidence | visual margin (20.48 vs 15) | `score.py:ambiguity` (15% tie, 400 m radius) | **different**; neither has a calibrated one |
| Speed of sound | constant, unstated | from `environment.temperature_c`; `temperature_search_c` exists in config but **no code reads it** | both fixed; **missing on both** |
| Wind | — | tolerance allowance only (`wind_sys_mps`) | scoring term **missing on both** |
| RPM / f0 drift | — | drift nuisance in `fit_ridge` only; constant f0 in scoring | scoring term **missing on both** |
| Harmonics | f0 slid by hand; harmonics visible but unused | `ridge.harmonic_check`, `mask_family`, `fit.is_harmonic_of`, `dedupe_same_aircraft` | **missing on theirs** |
| Mic height | — (camera on a pole) | fixed `mic.height_agl_m` | **missing on both** (low value, §3.2) |
| Missing aircraft | not addressed | `max_unmatched_events`, unknown class, `silence_mode: count` | **missing on theirs** |
| Water / DEM nodata | answer is "honestly in water … right on this bank" (L730) | `geo.py:build_tile` **drops every cell whose DEM sample is NaN** (`ok = np.isfinite(h)`) | **risk on ours** (change #2) |
| Validation | one real case, confirmed visually | synthetic plus realism layer; no real data yet | real **missing on ours**; quantitative **missing on theirs** |
| Countermeasures | demos | milestone 8; `doppler/countermeasures.py` is in CLAUDE.md's layout but **does not exist** yet | **missing on ours** |

---

## 3. The open questions in HANDOFF.md

### 3.1 Strict vs robust design variants

Their pipeline is **structurally the original strict design**: every chosen event must match a track,
and silence is a hard reject. It works for them because of three things our strict variant does not
have:

1. **A human curates the events.** Only the two clear prop curves go in. The faint one is left out and
   cars and birds never enter. Most of our strict variant's failures (zero survivors in the realistic
   ablation) come from a spurious or duplicate event vetoing every cell. Their pipeline never sees those
   events.
2. **Silence only applies between the two events**, the stretch where the recording is known to be
   quiet and the detector would have noticed a pass.
3. **"Conservative" thresholds**, meaning reject only when a pass would surely be heard. Our probabilistic
   mode's hard part does the same thing already: P ≥ 0.99 with σ = 6 dB means 20·log10(thr/r) ≥ 14 dB,
   so r ≤ thr/5 ≈ 560 m for props. Their silence is roughly our probabilistic mode without the soft
   penalty.

So they don't argue for strict over robust. They show that **strict works when every event is
trustworthy**. That suggests a better hybrid than HANDOFF's "strict first, fall back if zero survivors":

- **Confidence-tiered events (recommended hybrid).** Mark each event as an *anchor* or *soft* from
  measurements we already have: relative σ_v and σ_d from the fit covariance, coverage on both sides
  (`EventFit.coverage`), a harmonic stack present, class not unknown, gof. Anchors must match (strict).
  Soft events may stay unmatched (robust, lexicographic). With ≥ 2 anchors we get strict-design pruning
  and speed. With fewer we degrade gracefully. This is the automated version of their curation.
- **Why not strict-first fallback:** "zero survivors" is not a reliable trigger. Their 73,000 airport
  points (L317–326) show that in dense traffic a spurious event usually finds *some* matching track
  somewhere, so strict returns a confident wrong answer instead of zero survivors. Tiering decides per
  event before the cascade, so it doesn't depend on that trigger.
- **Silence with teeth where the clip is quiet.** Condition P(audible) on the **measured local noise
  floor** at the pass's received time (the detection statistic's baseline; `detect.tonal_statistic`
  already computes it), not on a fixed slant threshold alone. In quiet stretches, like their
  15 minutes of nothing, silence becomes strong again. During wind gusts, car passes or speech it
  correctly weakens. This answers HANDOFF's "silence nearly toothless beyond ~0.2× threshold" without
  re-tuning σ.

### 3.2 The realism gap (true cell 5th–39th with everything on)

Their method handles **none** of RPM, c, wind, mic height or ground reflection explicitly. It is still
informative, because two of its features attack the mechanism that I think causes our gap.

**Order-of-magnitude budget of the systematic frequency errors** (100 Hz tone, v = 60 m/s, c = 343),
compared with our ridge σ (floor 0.05 Hz, typically 0.05–0.3 Hz):

| Effect (realism setting) | Size on the curve | What absorbs it now |
|---|---|---|
| RPM ramp 3–10 % over 10–40 s (30 % of events) | **3–10 Hz** step-like | nothing; Huber caps per-point loss, but the location moves to fit the ramp |
| RPM walk 0.3 % | ~0.3 Hz, correlated over ~2 s | nothing |
| Wind 10 m/s along track | plateaus shift −0.75 % / −0.37 %: common mode → f0; **differential ≈ 0.38 Hz** (≈ 4v²w/c³) | location |
| ADS-B speed quantization 1 kt | ±0.15 % on each plateau ≈ **0.15 Hz** spread error | location |
| c mismatch ±3 m/s | ≈ 0.9 % of the excursion: ~0.15 Hz spread, delay error ≲ 0.08 s at 3 km | f0 (partly), location |
| ADS-B timestamp bias ±0.6 s per track | relative timing error up to 1.2 s between events → ~40 m along-track per track | location (Δt is shared, so it cannot) |
| Mic height 1–10 m vs 1.5 m assumed | slant error ≤ 8.5 m, delay ≤ 0.025 s | negligible for geometry |
| Ground reflection, mic height h | notches every c/(2h·sinθ): about 380 Hz at h = 1.5 m, about 57 Hz at h = 10 m, sweeping as θ changes | ridge drop-outs and **harmonic hops** |

Reading the table:

- **Our σ is the ridge's white scatter, but the residuals are dominated by systematic model error that
  is 3–100× larger.** Every one of these effects is a "many-σ" outlier. The pooled cost is then
  dominated by whichever event has the cleanest ridge (smallest σ) and the largest systematic, and the
  location shifts to absorb it. Each group alone is small enough not to win. All together they add up.
  This fits rank 1–2 per group and 5–39 with all on. Their brightness score has a built-in tolerance of
  about one spectrogram bin, so it is insensitive to sub-Hz biases. That is not more precise, but it
  does not let a 0.4 Hz bias drag the location.
- **Mic height is not the problem geometrically.** A mic-height search (HANDOFF step 2) would buy
  ≲ 10 m. Mic height matters through reflection notches, and that is a ridge problem, not a model
  problem.
- **RPM ramps are by far the largest single effect**, and a linear f0 drift only partly absorbs a tanh
  ramp. A very flexible f0(t) would be worse: the Doppler transition is itself a smooth ~35 % frequency
  change over d/v ≈ 5–30 s, so a spline f0 with knots every ~10 s would absorb the location
  information. In scoring, v is fixed by ADS-B, so a *linear* drift is identifiable there (it is
  degenerate with v only in the per-event fit). Anything beyond linear needs a strong prior and the
  ablation to prove it doesn't flatten the cost surface.

Their method suggests two specific fixes: an error scale that includes model error (their bin
tolerance), and a harmonic-agnostic or comb-power score (their brightness sum doesn't care which
harmonic carries the energy). Neither is in their method in a principled form. See changes #3–#7.

### 3.3 Wind physics and our tolerances

The transcript never mentions wind, so there is nothing to compare against. Our corrected physics
(Doppler v tracks ADS-B ground speed, CLAUDE.md Wind pitfall) means the right comparison is directly
against ADS-B ground speed. The numbers above confirm HANDOFF's estimate: a 10 m/s wind biases the
inverted v by about 0.25–0.65 m/s. The current `wind_sys_mps: 15` is about 20× that, and `d·w/v` inherits
the same factor.

Their v ranges were also generous (100 vs 70 m/s, i.e. ±20–30 %), and they paid for it with 73,000 →
15,000 survivors. Our 15 m/s allowance does the same to us: the ~1 km silence-survivor band and
276 hypotheses in test_locate. But the 15 m/s currently also hides **real non-wind effects**: synthetic
speed ramps (10 %), turns and climbs, which bias the straight-line v. So if we tighten it, split the
allowance rather than just shrinking it (change #8):

- wind: derived from the wind prior, w_max·v/c ≈ 2 m/s (physics, not tuning);
- track kinematics: **per pass, from the ADS-B data itself**, as the spread of ground speed over
  ±k·d/v around that pass's CPA (a measurement, not a constant).

### 3.4 Things their real recording has that our synthetic realism misses or gets wrong

1. **Airport-density traffic.** Their survivors cluster at airports (L317–326). Our synthetic regions
   have 3–10 background tracks and 4 near-threshold aircraft. Pattern traffic (slow, low, turning,
   climbing props that repeatedly match v and d) is the real discrimination test for both variants,
   and it is exactly where strict-first fallback fails (§3.1).
2. **Shoreline / water sites.** Their answer is "honestly in water … right on this bank". Many DEMs
   (3DEP among them) are nodata over open water, and `geo.build_tile` silently drops those cells.
   Reflection over water is also stronger (R ≈ 1) than our 0.8–1.0 draw on land implies.
3. **Lossy codecs and gating.** Their audio went through camera compression and an MP3 export, and
   the phone demo shows noise suppression **removing the curve whenever nobody is talking**. Our
   recording chain has a high-pass and AGC but no lossy codec, no VAD/DTX gating and no
   noise-suppression drop-outs. These produce ridge gaps and edge-clipped segments that look like our
   `truncated_coverage` case but at arbitrary times.
4. **Day-long time prior.** Their main result uses date-only (24 h window). Our benchmark is
   known-time. Window mode over a day stresses the offset filter, hypothesis enumeration (Python loops,
   HANDOFF Fragile) and the dense TrackSet (CLAUDE.md pitfall: a day of CONUS traffic is far beyond
   0.6 GB).
5. **Sub-audible curves at high spectral resolution.** They needed to "increase the resolution" to
   see the faint curve. Our main spectrogram is fixed at 1 s / 1 Hz. Slow props with long passes gain
   tonal SNR from 2–4 s windows. The synthetic events are resampled to ≥ 0 dB band SNR, so we never
   test the regime where only a long-window spectrogram reveals the event.
6. **Mic on a pole (security camera).** That case is covered by our 1–10 m mic-height draw. Keep it.
7. **Velocity timestamps.** Not from the transcript, but surfaced by it (they sample "radial velocity
   … from the flight data" at 5 s steps, L463–467): OpenSky velocity comes from a different message than
   position. Since we re-time positions to `lastposupdate`, the reported velocity may lag. We use
   `velocity_source: adsb`. The synthetic latency bias is applied per track to both, so this mismatch
   is not modelled.

---

## 4. Ranked list of changes worth adopting

"Rules" = the CLAUDE.md sections each change touches. Every scoring change needs your sign-off
(methodology step 8). The standard measurement is the **leave-one-group-out ablation** from HANDOFF
next steps (scenarios A, C, G; drop each of adsb, levels, source, path, scene, geometry, plus RPM-only,
c/wind-only, mic-height-only), run on **both variants**. It reports rank, rank-1 error, assignment
correctness and survivors per stage, with `--clean` as a no-regression check.

| # | Change | Expected impact | Cost | Rules touched | How to measure |
|---|---|---|---|---|---|
| 1 | **Diagnose before fixing.** Add to the ablation script (and later run.json): is the rank-1 *assignment* correct; rank of the true assignment among best-per-assignment groups (their "best per flight pair"); score gap true vs rank-1; per-event residual signatures (pre/post plateau asymmetry = wind/speed; step = RPM ramp; harmonic ratio jumps = switching); refined-estimate bias split along- vs cross-track. | Tells us whether rank 5–39 is a **discrimination** failure (wrong tracks) or a **bias** failure (right tracks, shifted minimum). These need different fixes. Also likely shows "rank 39" overstates the failure, because neighbouring cells of one assignment fill the ranks. | Low (scratch script; run.json fields later) | none (Outputs) | The ablation itself |
| 2 | **Don't drop DEM-nodata cells.** Fill nodata as water at 0 m orthometric (+ geoid), or from a coarse fallback DEM. Report the count. | Prevents silently losing a true shoreline cell, which is what their real case was. | Low | Repo conventions (geo), Outputs (counts) | Unit test with a nodata patch; the milestone 7 coastal case |
| 3 | **Model-error floor in the scorer's σ:** σ_eff² = σ_white² + σ_model², with σ_model from a per-event physics budget (ADS-B speed quantization, wind prior, c prior, RPM-walk prior), all in config. | Stops one clean-but-biased event dominating the pooled cost. Restores the locked timing information from the other events. My best single bet for the gap. | Low | Step 8 wording ("units of each event's ridge scatter"); config | Ablation (all, source, path), both variants; clean must stay rank 1 |
| 4 | **Global atmosphere nuisance, locked like Δt:** one c (prior from `temperature_search_c`, already sanctioned in Unknowns) and one wind vector (w_e, w_n) with a Gaussian prior, entering as c′ = c + w·k̂ in `modeled_ratio`. | Removes the asymmetric wind shape error (~0.4 Hz at 100 Hz, 10 m/s) and the c spread error. Recovers c and w as diagnostics against truth JSON. | Medium. Refinement goes 3-D → 6-D, or profile c and w on a coarse grid. Score time ×3–10. | Step 8 free parameters; Unknowns (c range); Wind pitfall | c/wind-only and path ablations; recovered c and w vs truth |
| 5 | **Harmonic-agnostic residual:** each ridge point is compared with the nearest of a small set of ratios k·f0·ratio (½, 1, 3⁄2, 2, 3 relative to the event's order), with EM-style f0 profiling. | Fixes harmonic hops from reflection notches and rich stacks (source and path groups). Their brightness score is immune to this, and ours isn't. | Low–medium | Step 8 (cost definition); Harmonics pitfall | source, path ablations; count of hop segments |
| 6 | **Per-event linear f0 drift in scoring** with a prior (reuse `fit.f0_drift_prior_per_s`). Nothing more flexible without proof. | Partly absorbs RPM ramps and walk. Linear is identifiable in scoring because v comes from ADS-B. | Low | Step 8 free parameters | RPM-only ablation; check the clean gap ratio does not shrink (lost discrimination) |
| 7 | **Comb-power (brightness) score as a second opinion** on the top-K cells: sum of clipped excess dB along k·f0·ratio(t) over 1–3 harmonics, per event, normalised per event. Same machinery as `ridge.doppler_hough`. | Robust cross-check where ridges are broken (faint curves, gaps, crossings). Disagreement with the ridge score is a useful ambiguity flag. Don't replace the ridge score: they found brightness fails on faint curves. | Medium (spectrogram windows recomputed per event, not cached) | Step 8 (or diagnostics only, if it never feeds ranking); Storage (no caching) | Rank agreement on the ablation; faint-SNR sweep |
| 8 | **Split `wind_sys_mps`** into a derived wind term (~w_max·v/c) and a per-pass kinematic term from ADS-B speed spread around CPA. | Fewer CPA/offset survivors and hypotheses, so faster scoring and fewer wrong assignments, with no loss on maneuvering tracks. | Low–medium | Known pitfalls (Wind, provisional sys); Decisions §1 tolerance form | Survivors per stage + true-cell survival on all ablations, both variants |
| 9 | **Confidence-tiered events** (anchor = must match; soft = may be unmatched) as the strict/robust hybrid. | Strict-variant pruning when the events are good; robust behaviour otherwise; no unreliable zero-survivor trigger. | Medium | Steps 5–6; Operating modes (thresholds in config, provisional until 4.5) | Ablation as a **third variant** alongside the two; plus an airport-density scenario |
| 10 | **Noise-floor-aware silence:** P(audible) conditioned on the measured local background at the pass's received time. | Silence regains teeth in quiet stretches and stops wrongly rejecting during masking noise. | Medium | Step 6; Coverage-gaps pitfall | Decoy tests (hard and probabilistic) + scene ablation |
| 11 | **Realism additions:** airport pattern traffic, water/shoreline site, lossy codec + VAD gating, faint (< 0 dB) events, day-long window scenario. | Closes the gaps in §3.4 before real data arrives. | Medium | Validation; Countermeasures; Storage (FLAC, generated at test time) | Each one as its own ablation group |
| 12 | **Per-assignment ranking in outputs/viewer:** best cell per assignment, fine cost map around the top 2–3 assignments, and an assignment-gap ambiguity measure (score gap between the best two *distinct* assignments) alongside the spatial one. | Better confidence reporting: a real ambiguity is between assignments, not between neighbouring cells. | Low–medium | Outputs (read-only diagnostics); Operating modes (never feeds back) | Inspect on ablation runs; compare with `score.ambiguity` |
| 13 | *(Needs a rule decision)* **Soft per-track timing nuisance** with prior σ ≈ ADS-B latency (0.3 s), penalised in nats. | Absorbs per-track timestamp bias (adsb group, ~40 m per track). | Low | **Conflicts with "relative timing comes only from physics"** (step 8, Locked-scoring pitfall). Defensible only because the error is in ADS-B time, not audio. | adsb ablation; confirm the offset-filter discrimination is unchanged |

Deprioritised: **mic-height search** (HANDOFF step 2). Its geometric effect is ≲ 10 m (§3.2). It is
cheap, so fold it in only if #1 shows a vertical bias.

---

## 5. Things we do better (keep them)

- **Full automation, no post-hoc tuning.** They pick events, fit ranges and trace the faint curve by
  hand, and describe their normal practice as "looking at some of the points, seeing why they don't work,
  and then encoding that logic as another filter" (L370–373). That is tuning after looking at results.
  Our `human_in_loop: false` record, and the rule against tuning provisional thresholds on synthetic data,
  are what make our validation claims mean something.
- **Covariance-derived tolerances** (`fit_ridge` + `event_tolerances`) instead of eyeballed generous
  ranges.
- **Correct height datum** (`geo.GeoidModel`): the geoid near their site is about −32 m (see §6).
- **Continuous in-cell refinement** (`refine_in_cell`) instead of re-gridding to 100 m. It has no
  residual quantization and costs ~0.2 s per cell instead of 4× the points.
- **N-event shared-Δt offset filter**, and **whole-clip silence** with the CPA explanation rule and
  minimum audible duration. Theirs covers only the stretch between two events.
- **Unmatched events and aircraft without ADS-B** handled explicitly.
- **Harmonic handling** (subharmonic check, family masking, dedupe of one aircraft seen on two tone
  families).
- **A synthetic realism layer with ablation.** They have n = 1 and no error analysis.
- **Storage discipline:** cropped Parquet vs their 50 GB files.

---

## 6. Claims in the transcript that I think are wrong or doubtful

1. **"Altitude is the height above sea level" (L173–174). Wrong.** ADS-B geometric altitude is height
   above the WGS84 ellipsoid. Barometric altitude is pressure altitude in the standard atmosphere, which
   can be off by hundreds of feet on non-standard days (≈ 8 m per hPa). Near (40.87, −73.5) the geoid
   undulation is about −32 m. Comparing either with an orthometric DEM biases the slant distance by
   tens of metres. Their generous tolerances hid it.
2. **The single-event demo as independent confirmation (L652–694). Doubtful.** (a) With one event the
   along-track position is set by t0 and the clock: the error is v × time uncertainty. "8:37 p.m." to
   the minute would leave several km unless the time was known to seconds, and they don't say how it
   was known. (b) If the precise time came from the two-event solution's Δt, the demo is circular, and
   "the same location … across two different methodologies" is not independent. (c) A straight pass
   leaves a left/right mirror ambiguity that only track curvature breaks. The demo is consistent with
   our Single-event pitfall, but it shows the pitfall as worded is too broad. With a precise clock, one
   event gives two mirror points plus a v·δt along-track spread, not an extended locus.
3. **"High pass doesn't work" (L752–771). Overgeneralised from one demo.** Boosting after a high-pass
   recovers the curve only because the filter's stopband is finite and the event is loud. The physical
   reason it fails generally is different: prop **harmonics above the cutoff** carry the same Doppler
   shape, and f0 is free. A steep filter on a faint event would remove it. Our countermeasures analysis
   should report this against SNR, not as a yes/no.
4. **"You can't drown it out" (L777–780). Overgeneralised.** The demo masks with intermittent speech. A
   narrow Doppler tone gains seconds of integration against speech that moves around in frequency.
   Continuous masking in the same band (music with bass, broadband noise) at a high enough level will
   mask it. This is a level question, so it needs our SNR sweep.
5. **"It can survive phone compression" (L792–810). Half true by their own demo.** Noise suppression
   removed the curve while he was silent. Narrowband calls (300–3400 Hz) also remove prop fundamentals,
   leaving only harmonics. Wideband calls pass them. Survival depends on the codec and on speech
   activity.
6. **"Within a couple hundred feet" (L719–722).** Plausible for one case found visually afterwards. It
   is one sample, with no error bar, and the authors knew the answer while presenting the scoring.
7. **"CPA distance determines how quickly that drop occurs" (L254–256). Imprecise.** The transition time
   is d/v and the peak slope is f0·v²/(c·d), so v and d trade off. It only holds with v fixed (their
   next step does fix v, so it's harmless).
8. **"You're probably in the only place on Earth hearing that exact curve" (L832–834).** That is
   rhetoric. Their own 15,000 survivors and close runner-ups show near-degenerate solutions are common
   without locked multi-event timing.

Correct and worth noting: the warning that the non-retarded model fails (L472–506) matches our physics
exactly, and the "up to 141 m off" grid argument (L579–580) is our half-diagonal / grid-quantization
pitfall.

---

## 7. Decisions needed before implementing anything

1. **Order of work:** run diagnostic #1 (inside the leave-one-group-out ablation) before any scoring
   change? (Recommended: yes. It decides between bias fixes #3–#6 and discrimination fixes #9–#10.)
2. **Scoring free parameters (step 8):** which of these may be added: global c, a global wind vector,
   a per-event linear f0 drift, and (separately) the soft per-track ADS-B timing nuisance (#13), which
   bends the "timing only from physics" rule?
3. **Error scale:** may σ in the score include a physics-budget model-error term (#3), changing the
   step 8 wording "in units of each event's ridge scatter"?
4. **Third design variant:** add confidence-tiered events (#9) as a third variant to compare against
   both existing ones, instead of the strict-first fallback?
5. **Wind tolerance:** split `wind_sys_mps` into a derived wind term plus a per-pass ADS-B kinematic
   term (#8) now, or wait for milestone 4.5 calibration as CLAUDE.md currently says?
6. **Silence:** make audibility depend on the measured local noise floor (#10)? This changes step 6 and
   needs a source-level model that stays provisional until 4.5.
7. **Comb-power score (#7):** diagnostic only, or allowed to affect ranking?
8. **DEM nodata (#2):** treat nodata as water at 0 m orthometric, or require a fallback DEM?
9. **Realism additions (#11):** which of airport traffic, shoreline/water, lossy codec + VAD gating,
   sub-0 dB events and a day-long window scenario go into the ablation now, and which wait for the
   countermeasures milestone?
10. **Single-event pitfall wording:** refine it to "locus length = v × clock uncertainty, plus a mirror
    ambiguity", and report single-event results against `clock_tol_s`?
