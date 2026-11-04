# HANDOFF — Acoustic Doppler Geolocation

Read CLAUDE.md first; it is authoritative for methodology and conventions. This file
records session state, decisions, and what is fragile.

## START HERE (end of session 3, commit 1894c68 + this handoff)
Where things stand: milestone 5 passes on *clean* synthetic data. A skeptic review showed the clean
synth was far too favourable, so a realism layer was added. With **all** realism on, the true cell
survives every cascade stage but **ranks 5–39** (errors 100–410 m). Each realism group alone gives
rank 1–2. The SNR / tolerance / DEM sensitivity sweep the user asked for has **not** been run yet:
do it after the scoring robustness below, on realistic scenarios.

### Next steps, in order
1. **Leave-one-group-out ablation** (lost when the session crashed). Script:
   `/private/tmp/.../scratchpad/scripts/ablate.py` is scratch and may be gone; recreate it. Per
   scenario (A: 600 s 150,420; C: 60 s 15,45; G: 300 s 50,150,250), run synth with every realism
   toggle on except one group (adsb, levels, source, path, scene, geometry; also RPM-only, c/wind-only,
   mic-height-only), then `locate --no-plots`. Record rank, error, and per-event fit errors vs truth
   (dv, dd, dt against their tolerances). This shows which effects break *scoring* in combination.
2. Likely scoring fixes (each needs user sign-off, because they touch CLAUDE.md methodology step 8):
   - **per-event f0 drift** nuisance (f0·(1+β·(t−t_cpa))) for RPM ramps (3–10% in 30% of events);
     constant f0 per event cannot fit them.
   - **speed-of-sound search** over environment.temperature_search_c (CLAUDE.md already allows a
     range); synth now mismatches c by ±3 m/s.
   - mic height: the pipeline assumes 1.5 m and synth draws 1–10 m; could search it like Δt (cheap).
3. Then the user's sweep: SNR, tolerance settings (n_sigma, sys values), DEM error (bias/noise via
   region.ground_elev_m or a perturbed DEM), on realistic scenarios plus `--clean`.

### Two design variants to keep (user, end of session 3)
The user accepted the two changes below as the **current defaults** but wants the **original design kept**
so both can be compared later and the advantages of each used. Don't delete either path; any comparison
(ablation, sweep, real data) should report both variants side by side.

| | Original design (milestones 4–5) | Current default (session 3, robustness) |
|---|---|---|
| Unmatched events | every event must match a track (`cascade.max_unmatched_events: 0`) | up to 1 event may stay unmatched (`: 1`), lexicographic ranking (explain more events first, then score) |
| Silence | hard: reject a cell if any unexplained class-compatible pass is within the slant threshold (`cascade.silence_audibility: hard`) | probabilistic: reject only if P(audible) ≥ 0.99, else add −ln(1−P) nats to the score (`: probabilistic`) |
| Strengths | strong pruning, few survivors, fast scoring; decoys are decisive; matches CLAUDE.md steps 5–6 as written | survives spurious events (cars, birds, harmonics), aircraft without ADS-B, and quiet aircraft inside the threshold |
| Weaknesses | one spurious event or one quiet nearby aircraft eliminates the true cell (zero survivors in the realistic ablation) | many more survivors (single-event loci), slower scoring (276 hypotheses, 23 s); silence nearly toothless beyond ~0.2× threshold |

Run the original with: `cascade: {max_unmatched_events: 0, silence_audibility: hard}` (the decoy tests in
tests/test_cascade.py use exactly this, as `STRICT`). Possible combination to test later: strict first,
falling back to the robust variant only when strict leaves zero survivors (or when an event has no
class-compatible track within reach). CLAUDE.md methodology steps 5–6 still describe the original; update
them only once the comparison settles it.

### Other open decisions for the user (implemented as defaults, not yet approved)
- `cascade.max_unmatched_events: 1`: an event may stay unmatched (car, bird, aircraft without ADS-B).
  Ranking is **lexicographic**: hypotheses explaining more events always rank above those explaining
  fewer; then by score. (A fixed per-point penalty in σ units failed: with small ridge σ even the true
  fit cost more than "unmatched".) Side effect: many more cascade survivors (single-event loci), and
  scoring time grows (276 hypotheses, 23 s in test_locate).
- `cascade.silence_audibility: probabilistic`: P(audible) = Φ(20·log10(thr/r_min)/σ), σ = 6 dB
  (`silence_level_sigma_db`, PROVISIONAL). Reject a cell only if an unexplained pass has P ≥ 0.99, else
  add −ln(1−P) nats to the score (`score.silence_weight`). This makes silence much weaker: a decoy at
  0.8× threshold adds about 1 nat. That is physically honest given ±6 dB level scatter, but it changes
  CLAUDE.md step 6 ("reject"). `hard` keeps the milestone-4 behaviour (decoy tests use it).
- `cascade.gof_widening: true`: σ × max(1, fit rms / ridge noise), the reduced-χ² scaling (covariance-
  derived, inside the approved tolerance form).
- **Wind tolerance:** the physics in CLAUDE.md was wrong (Doppler v ≈ ground speed, now corrected in
  Known pitfalls). The 15 m/s wind_sys and the d·w/v term are far larger than needed (≈ v·w/c ≈ 2 m/s).
  Tightening is the user's call; left unchanged.
- Ambiguity measure (user said "up to you"): tied set = score ≤ best·(1+0.15) among hypotheses
  with the same number of unmatched events; "unique" if within 400 m of rank 1. Validation counts
  "correct" only if rank 1 *and* unique. Thresholds PROVISIONAL.

### Realism layer (doppler/realism.py; config.yaml `realism:`; `synth --clean` = all off)
From the skeptic review (ranked findings, every one implemented as a toggle):
- ADS-B: 1 s ±0.3 s fixes, per-track timestamp bias U(±0.6 s), σ 7 m (3% MLAT 100 m), 25 ft altitude
  steps, 30% of props baro-only with N(0, 60 m) offset, 1 kt / 64 fpm steps, Poisson gaps 5–60 s,
  optional dropped aircraft (events protected).
- Source: shared level N(class mean, 6 dB) for all aircraft (events resampled to ≥ 0 dB band SNR at CPA),
  10 harmonics at −3 dB/order ±6 dB plus a −12 dB half-order comb, RPM walk 0.3% + ramps 3–10% (30%),
  twin-engine detune (30% of props), directivity ±6 dB (jets: fan tone fades aft).
- Path: ISO 9613-1 absorption per harmonic (verified against the standard's table), ground-reflection
  image source R 0.8–1.0, uniform wind ≤ 10 m/s, c mismatch ±3 m/s.
- Scene: wind gusts, 60 Hz hum comb, car passes (Doppler), bird chirps 2–6 kHz, 100 Hz high-pass, AGC.
- Geometry: 50% corridor-aligned events, climbs ±3–5 m/s (40% props), speed ramps, turns ≤ 3°/s,
  clock offset ±5 s, mic height 1–10 m, 4 aircraft in the 0.5–1.5× threshold band, offsets 0–1500 m,
  prop speeds 35–90 m/s.
- Truth JSON records c_true, wind, clock error, true mic height, ADS-B degradation per aircraft, and
  each aircraft's level / closest in-clip slant / peak band SNR.
- Clean scenarios are bit-identical to before (checked by hash). Synthesis of a 600 s realistic clip
  takes ~40 s (hot spot: per-harmonic complex recursion over 2 paths × twins).

### Ablation results (each group alone; pre-leave-one-out). rank / rank-1 error
| group | A (600 s) | C (60 s) | G (3 events) |
|---|---|---|---|
| clean (wider offsets/speeds) | 1 / 37 m | 1 / 3 m | 1 / 8 m |
| adsb | 1 / 48 | 1 / 33 | 1 / 77 |
| levels | 1 / 34 | 1 / 11 | 2 / 46 |
| source | 2 / 95 | 1 / 4 | 1 / 14 |
| path | 2 / 66 | 2 / 165 | 1 / 17 |
| scene | 1 / 26 | 1 / 3 | 1 / 9 |
| geometry | 1 / 60 | 1 / 8 | 1 / 21 |
| **all** | **39 / 410** | **5 / 261** | **32 / 100** |
Before the robustness fixes, source / path / levels / all mostly had **zero** CPA survivors (spurious
or duplicate events vetoed every cell) or a silence-rejected true cell.

### Pipeline changes this round (tests in test_realism, test_fit, test_cascade, test_score)
- fit: `dedupe_same_aircraft` (same t_rx, v, d; f0 at a simple ratio) and `is_harmonic_of` (jet-band ridge
  on integer multiples of a prop event's fitted curve); `classify.min_aircraft_speed_mps` 25 (PROVISIONAL).
- tracks: OpenSky `lastposupdate` used as the position time (needed for milestone 7).
- Tests: 104 fast pass; slow `test_locate` / `test_cli_export` pinned to `synth --clean` and pass; the
  rest of the slow suite was not rerun after this round.

## Status (milestone 5)
- **Milestone 5 done** (commit e181f21): locked N-event scoring with in-cell refinement; the synthetic
  true cell ranks 1st in every multi-event scenario (table below). Read-only comparison viewer built.
- Automation first (CLAUDE.md Operating modes): human-in-the-loop work is deferred to milestone 9.

## Status (end of milestone 4)
- Milestones 1–3 done (tag `milestone-3` = e742f5c; created in session 3).
- **Milestone 4 done** (commit c47e618 + this handoff): streamed cascade, outputs contract, plots/viewer,
  QGIS template. The synthetic true cell survives every stage in all validation scenarios below.
- Next: milestone 4.5 needs the user's known-location field recordings; milestone 5 (locked N-event
  scoring) can start on synthetic data independently.

## How to run
```bash
.venv/bin/python cli.py synth --seed 0 --event-times 150,420 --duration-s 600 --cascade-decoys 3
.venv/bin/python cli.py locate --scenario outputs/scenarios/synth_seed0      # -> outputs/<run_id>/, outputs/latest
.venv/bin/python cli.py clean --keep 5
.venv/bin/python cli.py regions list        # or: regions add --name X --lat .. --lon .. --radius-m ..
QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python -s qgis/build_project.py [--verify]
.venv/bin/python -m pytest -m "not slow"    # ~45 s;  -m slow ~6.5 min
```
QGIS's Python must run with `-s` (user site-packages in ~/.local break its shapely import).

## Milestone 4 — what exists
- `doppler/cascade.py`: `iter_cascade` yields one `TileResult` per active tile (full grid never in RAM
  or on disk). Tiles out of reach of every event's track samples are skipped before the DEM is read.
  CPA prefilter (cKDTree sparse distance matrix + refinement on the linear-interpolated polyline) →
  offset filter (shared Δt: ∩ over events of ∪ over passes, vectorized; then `HypothesisTable`
  enumeration with distinct aircraft unless `score.allow_shared_tracks`) → silence filter.
- Interfaces fixed for N events: `ObservedEvent`, `Tolerances`, `DtPrior`, `PassTable`,
  `HypothesisTable`, `Hypothesis(cell_id, assignment, dt_bounds_s)`; `score.ScoreParams`, `CellScore`,
  `modeled_ratio` (implemented, tested against physics.predicted_ratio_at), `profile_f0`,
  `score_hypotheses` (raises NotImplementedError — milestone 5).
- `cli.py locate` writes outputs/<run_id>/: results.gpkg (grid [regional or --write-grid], stage_cpa,
  stage_offset, stage_silence, candidates, tracks, event_cpa_points; all EPSG:4326), score.tif (COG,
  ZSTD, grid CRS, cropped, NaN nodata), run.json (config snapshot, events + tolerances, counts per stage,
  timings, truth evaluation, disk footprint), funnel.png, spectrogram_top.png, score_hist.png,
  viewer.html (uses ../plotly.min.js, plotly.js 2.35.2, sha256 in config.yaml).
- QGIS: qgis/doppler_template.qgz + qgis/styles/*.qml, regenerated by qgis/build_project.py; verified
  (every layer valid against outputs/latest, relative sources, group order). User's original partial
  doppler.qgz was removed from the repo root as asked (it is in git history at f6dc658).

## Decisions (session 3, approved by the user)
1. **Tolerances:** per event, `tol = √((n_σ · w_cov · σ)² + sys²)`; σ from the fit covariance (t_rx
   variance propagated from t0 and d), `w_cov = truncated_tol_mult` per edge-clipped side, `sys` absolute
   allowances (wind m/s; height m ⊕ d·wind/v; timing s ⊕ d·wind/c²). No percentage tolerances. Clock
   error only in the Δt prior (`time_prior.clock_tol_s`). `sys` values PROVISIONAL until 4.5.
   *Added in implementation (flag for review):* the grid-quantization bound (mic up to half a cell
   diagonal h from the cell centre) is added **linearly**: δd ≤ h, δT ≤ h/v + h/c. It is a hard bound,
   and putting it in quadrature can reject the true cell by construction.
2. **score.tif** in the grid's own CRS; vectors in EPSG:4326 (CLAUDE.md records the exception).
3. **Parquet:** fastparquet + cramjam, called through fastparquet's own API (pandas 3.1 deprecates its
   fastparquet engine). Switch to pyarrow if a later dependency (pyopensky) pulls it in.
4. **plotly.min.js** 2.35.2 downloaded once to outputs/plotly.min.js; `clean` never deletes it. If it is
   lost: `curl -sSfL -o outputs/plotly.min.js https://cdn.plot.ly/plotly-2.35.2.min.js` and check the sha256.
5. Old outputs/synth_* runs deleted. User runs `pip cache purge` themself.
6. Synthetic audio is 24-bit FLAC; tracks are zstd Parquet.
7. **Geoid:** GEOID18 + EGM96 grids in data/proj/ (gitignored, 19 MB, copied from QGIS.app);
   `geo.GeoidModel` uses the first grid covering a point, warns and uses `dem.geoid_undulation_m`
   when no grid opens or a point is outside every grid.
8. **N events** everywhere; two is the baseline.
9. **Provisional** thresholds marked in config.yaml; don't tune them against synthetic data.
10. Before milestone 5, `candidates.score/rank/f0_e*` are null; `interim_rank`/`interim_cost` (RMS |z| of
    v, d, t at the Δt midpoint) fill the gap and score.tif carries the interim value (band description says so).
11. QGIS template built headlessly with QGIS's Python, not the MCP. User sets the QGIS network cache.
12. Search regions by name from inputs/search_regions.gpkg (seeded with `synth_boulder`, 6 km circle).
13. DEM access is windowed per tile (`cli.RasterDEMSource`, rasterio windowed reads of a raster/VRT).

## Decisions made during implementation (not yet reviewed by the user)
- **Silence, unknown class:** `audible_slant_m.unknown` = the smallest class threshold (3000 m), so the
  silence filter stays lenient on type-code gaps. With 6000 m (the old value) an unregistered prop 4.4 km
  away rejected the true cell (seed 4, 1000 s scenario). This is a consistency fix, not tuning.
- **Silence, explanation rule** (`cascade.silence_explain`, default `cpa`): an audible pass is explained
  only if that track passed the CPA prefilter at that cell for some event (v, d, time consistent) and its
  in-clip closest approach is within `explain_time_tol_s` of that event. The old rule (any compatible event
  within 20 s) let a decoy overflying at the same time as the real event excuse itself. `time` keeps the
  lenient rule.
- **Silence audibility duration:** a pass counts only if audible (slant < class threshold) for
  ≥ `silence_min_audible_s` (20 s, ≈ detect.min_duration_s) of received time inside the clip.
- **Cascade decoys (synth):** `synth --cascade-decoys N` runs CPA + offset on the scenario, places prop
  decoys over survivors far enough from the mic (≥ 0.4 × threshold = 1200 m), rebuilds the scenario with
  a separate random stream (events unchanged). Mirror-cell decoys from milestone 3 are inert for N ≥ 2
  (mirror cells already fail CPA/offset), which is why these were added. When every survivor is within
  1200 m of the mic no decoy is feasible (reported, not an error).
- `make_scenario` now always draws the random mic position, so passing `mic_enu` no longer shifts the
  random stream (previously it silently produced different events).
- results.gpkg: R-trees only for layers ≥ 50k features; VACUUM on close; lat/lon attribute columns dropped
  from polygon layers (geometry has them).

## Fragile / watch
- **Dense TrackSet** at US scale (CLAUDE.md Known pitfalls); not fixed.
- **US-wide score.tif:** above `outputs.max_raster_cells` it is a sparse tiled ZSTD GeoTIFF, not a COG.
- **US-wide grid count:** exact up to 5000 tiles, otherwise area-based (run.json `grid_count_exact`).
- The template's `grid` layer is unavailable for US-wide runs (no grid layer written); QGIS will ask.
- Hypothesis enumeration and the silence explanation lookup loop in Python over survivors only; fine
  regionally, profile before US-wide.
- Per-event fit is straight-line: on curved tracks σ is optimistic; `timing_sys_s` and `w_cov` cover it.
- The interim cascade ranking is not a location estimate; with the provisional 15 m/s wind allowance the
  silence survivors form a ~1 km band around the true cell. Locked scoring (milestone 5) must rank.
- pandas 3.1 emits deprecation warnings from inside fastparquet (harmless).

## Milestone 4 validation (synthetic, audio end-to-end via `synth` → `locate`)
Regional 6 km circle, 200 m grid (2,821 cells, EPSG:32613), known time ±10 s, `--cascade-decoys 3`.
Rank/error are the *interim* cascade ranking (not a location estimate; scoring is milestone 5).

| scenario | events | CPA | offset | silence | silence removed | true cell | interim rank / err | run time (events / cascade) | run disk |
|---|---|---|---|---|---|---|---|---|---|
| A 600 s, 2 prop (150, 420 s), 1 decoy | 2 | 56 | 32 | 22 | 10 | all stages | 2 / 306 m | 13.4 s (11.8 / 0.04) | 2.40 MB |
| B 600 s, seed 1, 0 decoys feasible | 2 | 47 | 18 | 18 | 0 | all stages | 1 / 77 m | 12.4 s (10.9 / 0.04) | 2.34 MB |
| C 60 s, overlapping (15, 45 s), 3 decoys, edge-clipped (w_cov 2) | 2 | 86 | 43 | 26 | 17 | all stages | 1 / 62 m | 3.9 s (2.7 / 0.12) | 1.58 MB |
| D 600 s, prop + jet, 1 decoy | 2 | 39 | 25 | 23 | 2 | all stages | 2 / 120 m | 13.4 s (11.8 / 0.04) | 2.51 MB |
| E 120 s, single event, 3 decoys | 1 | 284 | 284 | 76 | 208 | all stages | 15 / 453 m | 6.6 s (5.1 / 0.07) | 2.02 MB |
| F 1000 s, 920 s offset (40, 960 s), 1 decoy | 2 | 53 | 38 | 26 | 12 | all stages | 1 / 87 m | 12.4 s (10.6 / 0.05) | 2.42 MB |
| G 300 s, three events, 0 decoys feasible | 3 | 47 | 21 | 21 | 0 | all stages | 2 / 143 m | 13.1 s (11.6 / 0.08) | 2.47 MB |

Runtime is dominated by audio event analysis; the cascade takes 30–120 ms per run. Scenario inputs:
3–27 MB each (24-bit FLAC dominates: noise does not compress). Fast tests also check, from truth-derived
events, that the true cell survives in five scenarios (1–3 events, prop+jet), that cascade decoys' target
cells reach the silence stage and are rejected there, that tiling (7 vs 64 cells) doesn't change survivors,
and that `silence_mode: count` keeps cells.

Not yet reported (CLAUDE.md Validation): sensitivity to SNR, tolerance settings, and DEM error.


## Milestone 5 — locked scoring (synthetic, audio end-to-end via `synth` → `locate`)
Same scenarios as the milestone-4 table. Score = pooled mean Huber loss (ridge-scatter units); each cell
scored at its best in-cell point. Error = rank-1 refined estimate vs the true mic.

| scenario | candidates | true cell rank (centre-only / interim) | rank-1 error | assignment | score time | run time | run disk |
|---|---|---|---|---|---|---|---|
| A 600 s, 2 events | 22 | **1** (2 / 2) | 52 m | correct | 5.4 s | 18.7 s | 2.02 MB |
| B 600 s, seed 1 | 18 | **1** (– / 1) | 9 m | correct | 3.2 s | 15.7 s | 1.83 MB |
| C 60 s, overlapping, edge-clipped | 26 | **1** (– / 1) | 3 m | correct | 4.3 s | 8.2 s | 1.39 MB |
| D 600 s, prop + jet | 23 | **1** (– / 2) | 22 m | correct | 7.7 s | 23.0 s | 2.04 MB |
| E 120 s, **single event** | 76 | 14 (– / 15) | 537 m | correct | 10.7 s | 16.9 s | 1.59 MB |
| F 1000 s, 920 s apart | 26 | **1** (– / 1) | 23 m | correct | 5.9 s | 18.1 s | 1.97 MB |
| G 300 s, three events | 21 | **1** (– / 2) | 8 m | correct | 5.4 s | 18.4 s | 2.13 MB |

E is expected: one event constrains only a locus (CLAUDE.md Known pitfalls). Centre-only scoring put A's
true cell 2nd (rank 1 was 306 m away along the cost valley); in-cell refinement fixed it. Fast tests also
check rank 1 from truth-derived ridges in four scenarios (incl. prop+jet and three events).

### Scoring notes
- Hot spot: in-cell refinement is ~0.2 s per cell (L-BFGS-B over east, north, Δt after a 3×3 start grid;
  ~1 ms per cost evaluation). Fine regionally; capped by `score.refine_max_cells`; profile before US-wide.
- σ per event = the fit's ridge scatter (`noise_hz`) on raw points; the scorer uses medians of 0.5 s bins, so
  absolute scores are smaller than a strict χ² would be. Ranking is unaffected; revisit if scores are ever
  turned into likelihoods or confidences.
- Mic height during refinement stays at the cell's DEM sample (terrain slope inside a cell is ignored).
- viewer.html live score matches the scorer to ~0.2% (it interpolates the modeled curve on a 0.25 s grid).
- Snapshot logs in the viewer are download-only (read-only diagnostics; nothing feeds back into a run).

### Not done yet
- Synthetic sensitivity to SNR, tolerance settings, and DEM error (CLAUDE.md Validation).
- No ambiguity/confidence measure: a single-event or otherwise degenerate clip still returns a rank 1.
