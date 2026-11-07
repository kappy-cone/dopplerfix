# Session history (moved from HANDOFF.md on 2026-11-06)

HANDOFF.md now holds only the current state. This file keeps the earlier session logs verbatim: summaries,
decisions, validation tables and notes from sessions 3–6. Parts are superseded; where they conflict with
HANDOFF.md, CLAUDE.md or a later report, the later document wins. Section headings are as they were.
Reports written before 2026-11-06 that cite "HANDOFF" (its next steps, Fragile list, ablation table or open
decisions) mean the sections now kept here.

---

## Session 6 start (superseded; U19 detection, plan §8.2, branch `s6-u19-detection`)
Read `NEXT_SESSION.md` (the session 6 prompt), CONTRIBUTING.md (branches, PRs, §7 CI), plan §8.2–8.4, and the
U18 section at the end of `reports/stageA.md`.
- **Where things stand.** Phase 1's P1.1 + P1.4 arms (V1c, V0c) failed U18: V1c's precision of unique is 61 %, but
  its rank-1 accuracy (status ignored) drops from V1's 34 to 25 on all-on C n = 100. They stay comparison arms.
  The U18 reference rows are in `reports/data/` (`stageA_rows.jsonl.gz`, `u18_rows.jsonl.gz`, plus per-unit
  detection counts).
- **U19.** Detection arms (a) harmonic-summed row, (b) 3 s long-window rows, (c) 20–100 Hz low-band row. All are
  in `doppler/detect.py`, off by default (V0/V1 rows unchanged), registered in `reports/apriori_registry.md`. The
  detection-only screen is `reports/experiments/u19/screen.py`. Stop points: after the screen, and after the
  pre-registered acceptance rule.
- **Where Stage A's missed events are lost** (baseline screen, all-on seeds 0–49): of 25 missed truth events,
  14 have a detection window but no fitted event (lost in ridge / fit), 6 never trigger, and 5 trigger nearby
  without a window. The §8.2 arms act only on the statistic. In `loo:path` and `sub:reflection` most misses never
  trigger.
- **Tests:** the fast suite takes about 2–4.5 min locally (CI shards it, CONTRIBUTING §7); the slow suite about
  6 min.
- **Waiting for the user, after U19's decision run** (`config.yaml` stays as it is until then, so the config
  hash is stable): `aircraft_types.type_table_path: data/doc8643_types.csv` (real tracks only), and whether to
  add `tracks.drop_onground` (a config key and a TrackParams field together).

## Session 5 summary (Stage A, §8.1 measures, profile, P1.1/P1.4 and U18)
**Session 5.** New machine set up: 157 tests passed (133
fast, 24 slow), then Stage A ran at `--jobs 8` in about 1 h 40 min. Read **`reports/stageA.md`** first.
- **Re-ordering rule: written order** (no failure class reaches 40 %; V1 is 37 % survival at CPA and 37 %
  discrimination). So Phase 1 goes next, starting with P1.1. The pilot's "P3.5 first" did not hold at n = 50.
- **All-on C, n = 50:** V1 7 correct, 32 abstained, 7 confident-wrong; V0 2 / 37 / 9; V1-asdesigned 12 / 18 / 12.
- **Missed target, reported (not tuned):** the provisional `inconsistent` status (`score.inconsistent_rms_sigma`
  3) labels 7 correct V1 answers as abstentions and does not separate right from wrong answers (39 % vs 45 %
  flagged). It is most of the V1 vs V1-asdesigned gap: their rank-1 answers are mostly identical. P1.1 / P1.4
  replace it; measure them against this baseline.
- Fixed: the as-designed wrapper recorded "no events detected" as `error` (commit 385a725; the 5 affected
  units were rerun).
- Setup notes for the compute machine: the system `python3` is not the project's; the venv is built from a
  separate Python 3.12. `../qgis-asdesigned/data/proj` is a symlink to `dopplerfix/data/proj`, because the
  as-designed arm runs `locate` from inside the worktree and would otherwise fall back to the constant geoid.

## Session 4 summary (Phase 0 code done, pilot run; moved to the user's PC via GitHub)
Session 4 did a design comparison against doppler.txt (a video in which someone builds a similar tool),
three independent skeptic reviews, a combined plan with an adversarial review, the user's decisions, and
**Phase 0** of that plan. Read in this order:
1. `reports/combined_plan.md`: the plan. §4 lists the decisions; §7 has the user's answers (all 17 USER
   decisions settled, including the success definition U10 and the weak-event option F1).
2. `reports/ablation_pilot.md`: the pilot results and the compute estimate for Stage A.
3. `reports/apriori_registry.md`: every new knob, with its source and sensitivity grid (P0.7 rule: never move
   a provisional value to hit a target).
4. Background, as needed: `reports/skeptic_*.md`, `reports/plan_review.md`, `reports/doppler_comparison.md`.

### Phase 0 status
- **Done, with tests** (157 passing incl. slow):
  - **P0.0:** variant overlays (`config.yaml variants: V0/V1`, `locate --variant`) and tag `variants-asdesigned`
    (= ea8fe0d).
  - **P0.1 a:** Newton seed clamped to track coverage.
  - **P0.1 b:** tile pruning respects `max_unmatched_events`.
  - **P0.1 h:** run.json `config_provenance` (overrides vs committed config) and `events_provenance`.
  - **P0.1 i:** best-first hypothesis enumeration (separate full/partial caps, budget), a CPA radius cap, and
    weak events (σ_d/d > 0.5) not gating pruning in V1 (off in V0, user F1).
  - **P0.2:** per-group, per-aircraft random streams with unconditional draws (`synth.realism_rng`,
    `realism.ABLATION_GROUPS`, `realism.without`). Hidden draws are recorded in truth.json. Clean scenarios are
    bit-identical; realistic ones are NOT the same as session 3's (re-baselined, as the plan says).
  - **P0.3:** `cli.run_locate` (in memory) + `write_outputs`; `LocateInputs`/`LocateResult`. Events seam:
    `events --out X.parquet` writes `X.meta.json`; `locate --events X.parquet` checks provenance, and any
    mismatch goes to `manual_inputs`.
  - **P0.4:** `doppler/evaluate.py` (outcome tiers per U10, failure taxonomy, fallback arms, re-ordering rule);
    the provisional `inconsistent` status (`score.absolute_fit_status`); `cli.py ablate` (paired, resumable
    rows.jsonl, `--jobs`, a per-variant wall-clock cap, a free-space guard, scenarios deleted after use).
  - **P0.5:** full slow suite run. **P0.7:** registry.
- **P0.6 pilot done** (reports/ablation_pilot.md). **Stage A done in session 5** (reports/stageA.md): the
  user chose to run it as is (`--jobs 8`, 900 s cap). `score.rank_cells` is still unprofiled; scoring is
  about 95 % of V1's time (CLAUDE.md: flag the hot spot before reaching for numba or multiprocessing).

### Running Stage A (on a committed, clean tree; the driver refuses a dirty tree without --allow-dirty)
```bash
git worktree add ../qgis-asdesigned variants-asdesigned      # the as-designed arms run from here
.venv/bin/python cli.py ablate --plan stageA --variants V0,V1 --jobs 3 --out outputs/ablation/stageA
.venv/bin/python cli.py ablate --plan stageA --limit 50 --variants V0-asdesigned,V1-asdesigned \
    --asdesigned-dir ../qgis-asdesigned --jobs 3 --out outputs/ablation/stageA   # all-on seeds 0-49 (plan: 0-19)
.venv/bin/python cli.py ablate --summarize --out outputs/ablation/stageA
```
The run is resumable: rerun the same command and finished rows are skipped, failed ("error") rows are
retried. The as-designed arms report only the old run.json fields.

### New machine setup (gitignored files)
- `.venv` from requirements.txt.
- `data/proj` geoid grids: `pyproj sync` (CLAUDE.md, Geoid pitfall).
- `outputs/plotly.min.js` (see Decisions §4 below for the URL and sha256).
- OpenSky: the user supplies credentials themselves through pyopensky's own config, never in the repo. If Trino
  access isn't granted by P4.3/P5.2, fall back to https://opensky-network.org/data/scientific.

### Next after Stage A (plan §3, dependency order)
The re-ordering rule is applied (reports/stageA.md: written order). Phase 1: P1.1
calibrated likelihood (arm), P1.2 assignment-level ranking, P1.3 ellipse, P1.4 ambiguity v2, P1.5 clock-prior
diagnostic, P1.6 reduced sweep. Still pending in CLAUDE.md: the single-event pitfall rewording (U7), the
Validation text (U10), and the two doc fixes in plan §7.

## Session 3 state (superseded where Phase 0 changed it)
Milestone 5 passes on *clean* synthetic data. With all realism on, the true cell ranked 5–39 (session-3
random streams; P0.2 changed them, so those numbers don't reproduce).

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
.venv/bin/python -m pytest -m "not slow"    # ~2-4.5 min;  -m slow ~6 min
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
