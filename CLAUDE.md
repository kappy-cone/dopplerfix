# CLAUDE.md — Acoustic Doppler Geolocation

## Goal
Estimate where an audio recording was made using the aircraft audible in its background. Each flyover produces a Doppler curve in the spectrogram. Given ADS-B flight track data for a region and time window, find the microphone location (and, if needed, the recording's time offset) whose modeled Doppler curves best match the observed ones.

Outputs: a ranked list of candidate locations, a score raster over the search region, and diagnostics, all viewable in QGIS. A companion analysis assesses which common audio processing does and doesn't defeat the method.

The repo serves two modes (see Operating modes): **fully automated** location estimation, which is the current focus and the basis of every validation claim, and later **human-in-the-loop** guidance, where an analyst uses the same scripts to narrow candidates faster. The two will be compared on real recordings to find where a human adds the most accuracy per unit of effort.

## Core idea
A single flyover constrains the mic heavily:
- **t0** (closest-approach time, corrected for sound travel time) ties the mic to where the aircraft was at that moment.
- **d** (CPA slant distance) plus aircraft altitude above ground gives the horizontal distance from the track.
- **v** (speed from the Doppler shift) and spectral character (prop vs. jet) identify which aircraft it could be.

Two events from different tracks, plus their measured time offset and the absence of other expected flyovers (silence), narrow the location to a small set of grid cells. Full curve modeling and locked N-event scoring (two events is the baseline) then rank those cells.

## Physics (source of truth — tests must verify these)
Straight, level pass:
- r(t) = sqrt(d² + v²(t − t0)²)
- f(t) = f0 · c / (c + dr/dt)
- c = 331.3 + 0.606·T_celsius m/s (default 343); configurable.

Closed-form inversions (used for per-event characterization and prefilter tolerances):
- v  = c · (f_pre − f_post) / (f_pre + f_post)
- f0 = 2 · f_pre · f_post / (f_pre + f_post)
- d  = f0 · v² / (c · maxRoC)

Reference check: f0=133, v=60, d=200, c=343 → f_pre=161.20 Hz, f_post=113.20 Hz, maxRoC=6.980 Hz/s.

General model (any track geometry, used for scoring): for candidate mic position P and track positions A(t_e), r(t_e) = |A(t_e) − P|. Sound arrives at t_r = t_e + r(t_e)/c, and the observed/source ratio is c / (c + dr/dt_e), using emission-time radial velocity. Modeled curves live on the received-time axis.

## Unknowns and priors
- **Mic location** (lat, lon). Ground elevation comes from a DEM per cell; mic height above ground is configurable (default 1.5 m).
- **Time:** known (absolute UTC start), window (search a range), or relative-only (only inter-event timing used, plus a searched global offset). The benchmark mode is known time.
- **Per-event f0:** nuisance parameter.
- **Speed of sound / temperature:** default value with an optional range.

## Methodology (authoritative)
1. **Track data:** ADS-B position, altitude (geometric preferred), and velocity for the search region and time window. Classify each aircraft as propeller or jet from its ICAO type designator, via an aircraft database lookup (icao24 → type code → engine type). Each audio event is labeled prop or jet from its spectral character (low-frequency prop/rotor harmonic stacks vs. broadband jet noise), and matched only against tracks of that class.
2. **Per-event characterization:** spectrogram → ridge extraction → fit (f0, v, d, t0) with covariance-based uncertainties. These are measurements with error bars, used for prefilter tolerances. The ridge points themselves are kept for scoring.
3. **Grid:** 200 m candidate grid (configurable) over the search region, each cell with DEM ground elevation.
4. **Per-cell CPA prefilter:** for each cell and each class-compatible track, compute CPA and propagation delay from 3D slant distance (track altitude vs. DEM elevation + mic height). Keep cell × track pairs consistent with each event's fitted v and CPA slant distance, within tolerance.
5. **Inter-event offset filter:** the received-time offsets between events' CPAs are measured from the audio (e.g. 920 s in the validation case; never hardcode it). Keep only cells where some assignment of tracks to events reproduces the observed received CPA times, including each cell's propagation delays, under one shared Δt within the time prior: the intersection over events of the union over candidate passes of each pass's feasible-Δt interval must be non-empty. This works for any number of events N; for N = 2 it is the pairwise offset test.
6. **Silence constraint (required):** reject cells where any class-compatible track would produce an audible pass during the clip with no corresponding event in the audio. Audibility uses a configurable slant-distance threshold per class.
7. **Full curve modeling:** for surviving cells, model the exact Doppler curve each event would produce from interpolated ADS-B positions, using emission-time radial velocity and propagation delay.
8. **Locked N-event scoring** (two events is the baseline): score modeled curves against the observed ridges. Free parameters per cell:
   - one global time shift Δt, applied identically to every event (absorbs CPA-time measurement error and clock offset)
   - one f0 per event (absorbs source-frequency uncertainty)

   The relative timing between events comes only from the physics (track positions + propagation), and is never fitted independently per event. Score = residual between ridge and modeled curves (pooled mean Huber loss in units of each event's ridge scatter; ridge points the track cannot model are penalised); rank cells by combined score. A cell is scored at its best point inside its footprint (bounded search of east, north and Δt), not at its centre: the cost surface is strongly anisotropic, so a centre-only score lets a cell 300 m away along the valley beat the true cell. The refined point is the location estimate.
9. **Outputs:** ranked cells with assigned tracks per event, Δt, and per-event f0; score raster (GeoTIFF); candidate, track, and grid layers (GeoPackage); diagnostic plots with every event's modeled curve overlaid on the spectrogram for the top candidates; per-stage survivor counts.

### Search modes
- **Regional:** bounded region, known or windowed time. Search regions are polygons in `inputs/search_regions.gpkg` (layer `search_regions`, EPSG:4326, field `name`), selected by name in config.yaml.
- **US-wide:** a single clip of known time. The cascade (steps 4–6) must prune with vectorized and spatially indexed operations (e.g. only cells within audible range of tracks active at the event times) before step 7. Never run full curve modeling on the full grid. Always report cell counts surviving each stage.

### Operating modes
- **Automated (current focus).** `cli.py locate` runs end to end with no human input. All validation (synthetic and the real benchmark) is fully automated: no manual event windows (`detect.manual_windows` empty), no hand-tuned per-clip parameters, nothing chosen after looking at the truth. run.json records `human_in_loop: false` and any manual inputs, so a run that used one is never reported as automated.
- **Human-in-the-loop (later, milestone 9).** An analyst reviews events, tracks and candidates and records decisions (e.g. reject an event, fix a harmonic, exclude a track or an area, reject a candidate) that the same pipeline consumes as data. Not built yet; designed and tested after the automated pipeline is validated on real recordings.
- **Design constraints now, so HITL can be added later without rework:** keep stages separable with explicit inputs and outputs (pure functions in doppler/*); no hidden state between stages; every threshold in config.yaml; outputs explain themselves (per-stage counts, assigned tracks, tolerances, and why cells fail where cheap). Don't build HITL tooling yet: no decision files, case workspaces, review servers, or analyst overrides.
- Read-only diagnostics (viewer.html comparison panels, plots, QGIS layers) are part of automated validation: they help a person check results but never feed back into a run.

## Repo layout
```
doppler/
  physics.py          # model, inversions, speed of sound, general modeled curve
  audio.py            # loading, time priors, spectrogram
  detect.py           # event detection
  ridge.py            # ridge extraction
  fit.py              # per-event characterization fit
  classify.py         # event prop/jet labeling from spectral character
  aircraft_types.py   # icao24 → type code → prop/jet lookup
  tracks.py           # ADS-B loading, cleaning, interpolation
  geo.py              # CRS/ENU conversions, DEM sampling, grids, spatial indexing
  cascade.py          # CPA prefilter, offset filter, silence filter, stage counts
  score.py            # full curve modeling + locked two-event scoring
  export.py           # GeoTIFF / GeoPackage / reports
  plot.py             # diagnostics
  synth.py            # synthetic scenarios: tracks + hidden mic + audio
  countermeasures.py  # apply audio processing and rerun the pipeline
cli.py                # subcommands: events, tracks, locate, synth, clean, countermeasures
inputs/               # committed: search_regions.gpkg
qgis/                 # committed: doppler_template.qgz, styles/*.qml, build_project.py
tests/
data/                 # gitignored: audio, tracks, DEMs, aircraft DB, proj/ geoid grids
outputs/              # gitignored
config.yaml           # search region, time prior, grid spacing, tolerances, bands, thresholds, temperature
```

## Conventions
- Python 3.11+: numpy, scipy, pandas, matplotlib, soundfile, pymap3d, pyproj, rasterio, geopandas, pyyaml, fastparquet (+cramjam). Tests with pytest. If a later dependency (e.g. pyopensky) pulls in pyarrow, switch Parquet to pyarrow and drop fastparquet rather than keeping both. Use a venv and pin requirements.txt.
- Internal time is float seconds since Unix epoch (UTC). Convert only at I/O boundaries.
- Do distance and Doppler geometry in ECEF (EPSG:4978, metres): exact 3D slant ranges anywhere, no tile seams. The candidate grid is indexed in a projected CRS (UTM for regional, EPSG:5070 for US-wide); local ENU is only for plotting and synthetic scenario construction. Store and export vectors in EPSG:4326. Exception: score.tif is written in the grid's own projected CRS (UTM / EPSG:5070) so raster cells align exactly with grid cells; reprojecting it would resample cells.
- Heights: ADS-B geometric altitude is height above the WGS84 ellipsoid; DEMs are usually orthometric, so add the geoid undulation from the geoid grid (data/proj/, see Known pitfalls) before comparing them.
- Clip length varies from under a minute to hours: never assume quiet background frames exist, and window any per-clip arrays (TrackSet, spectrograms) so memory scales with the time span actually needed.
- SI units everywhere; put units in variable names when ambiguous (alt_m, speed_mps).
- Pure functions in doppler/*. Keep file I/O in cli.py, export.py, and plot.py.
- Type hints and short docstrings on public functions. Every module gets tests.
- Vectorize the cascade and scoring with numpy. Flag hot spots before reaching for numba or multiprocessing.
- All tolerances and thresholds live in config.yaml, not in code.
- Explain any new dependency before adding it.

### Storage (disk is limited)
Optimize for storage wherever it costs no functionality or accuracy; when there is a real trade-off, ask.
- Never materialize the full search grid on disk. Write only cells that survive a stage. For US-wide runs, persist per-stage counts and survivors only; the full grid layer is written only for regional runs, or with `--write-grid`.
- Geometry and physics stay float64 (ECEF rule). Scores, dB spectrogram values, and display-only data may be float32 in outputs.
- Rasters: compressed, tiled Cloud-Optimized GeoTIFFs (DEFLATE or ZSTD), with nodata outside surviving areas, cropped to the extent of surviving cells.
- Tabular data (tracks, events, intermediates): Parquet with zstd, only the columns the pipeline uses, cropped to the needed time window and region.
- DEMs: download only tiles covering the search region; mosaic with a VRT rather than writing a merged copy. Read DEMs windowed per grid tile.
- Don't cache spectrograms or intermediates by default. Recompute unless caching saves meaningful time, and then cache behind a flag with a size cap.
- Synthetic test fixtures are generated at test time, not committed as WAVs. Synthetic scenario audio is written as 24-bit FLAC.
- outputs/latest is a symlink, not a copy. `cli.py clean` prunes old runs (keep the last N, configurable) and reports space freed. run.json records each run's disk footprint.
- viewer.html must work offline: reference one shared outputs/plotly.min.js (pinned version, recorded in config.yaml) instead of embedding it in every run, and downsample the spectrogram for display only.

## QGIS MCP (if connected)
- Use it for visualization and inspection only: loading outputs (score GeoTIFF, candidate, track, and grid GeoPackages) onto a satellite basemap, styling them, and checking results visually.
- Never put pipeline logic in QGIS. All computation (signal processing, fitting, grid generation, DEM sampling, filtering, scoring) lives in tested Python modules in doppler/. QGIS only reads files those modules export.
- Not needed before milestone 6. Don't set it up or call it earlier unless I ask.
- Avoid the arbitrary-code tool except for layer styling or loading tasks the standard tools can't do.

## Validation
- **Synthetic:** hidden mic, tracks of both classes, two events at a chosen offset, plus decoy tracks that must trigger the silence filter. The true cell must survive every cascade stage and rank 1st after scoring. Also report sensitivity to SNR, tolerance settings, and DEM error.
- **Real benchmark:** a 1-minute clip of known time, US-wide search. Success = the true location ranks 1st. Report the true location's rank and error distance, plus survivor counts per stage.
- **Automation first:** both of the above are fully automated runs (see Operating modes).
- **Human-in-the-loop evaluation (milestone 9):** real recordings with known locations and times, each run fully automated and with an analyst. The analyst is blind to the true location. Compare rank, error distance, and survivors per stage, and record cost: analyst minutes per intervention type (event review, harmonic fixes, track exclusions, area exclusions, candidate review) plus compute time. Report which interventions improve accuracy most per minute of analyst time, and on which kinds of clips (low SNR, edge-clipped passes, sparse ADS-B, few events) a human helps at all.

## Countermeasures analysis
Apply common audio processing to synthetic and real clips, rerun the full pipeline, and report:
- high-pass filtering
- masking with added noise or music ("drowning out")
- phone-call codec compression and noise reduction

For each: whether the true location still ranks 1st, how ridge SNR and fitted parameters change, and the physical reason it does or doesn't work (e.g. whether the Doppler *shape* and timing survive even when absolute level or low harmonics are removed).

## Milestones
1. Physics + tests. *(done in session 1)*
2. Synthetic audio + spectrogram + ridge + per-event fit. *(done in session 1)*
3. Tracks: ADS-B ingest, interpolation, type-code prop/jet classification, event classification, ENU conversion, DEM sampling. *(done in session 2, tag milestone-3)*
4. Cascade: grid generation, CPA prefilter, offset filter, silence filter, with stage counts. The synthetic true cell survives. (Also: geoid grids, covariance-derived tolerances, outputs contract, QGIS template, plots/viewer.) *(done in session 3)*
4.5. Real-data calibration: known-location recordings near an airport plus OpenSky tracks; set detection, classification, and audibility thresholds.
5. Full curve modeling + locked N-event scoring. The synthetic true cell ranks 1st. *(done in session 3; also the read-only comparison viewer)*
6. Exports + QGIS MCP visualization.
7. Real data: regional run, then the US-wide 1-minute benchmark.
8. Countermeasures analysis and report.
9. Human-in-the-loop guidance and evaluation: decisions-as-data consumed by the same pipeline, review tooling, then the automated-vs-HITL comparison on real recordings with known locations (see Validation).

## Known pitfalls
- **Locked scoring:** per-event characterization fits t0 independently; scoring must not. Don't reuse independent t0s as scoring parameters.
- **Wind:** Doppler-derived v is relative to the air mass, so it can differ from ADS-B ground speed. Keep the v tolerance generous and configurable. Cascade tolerances are √((n_σ·w_cov·σ)² + sys²): σ from the fit covariance, w_cov widening for edge-clipped passes, sys absolute physical allowances (wind, height, model error). Since d = v·τ, the wind allowance propagates into d. Clock error lives in the Δt prior, not in per-event tolerances. The sys values are provisional until milestone 4.5.
- **Provisional thresholds:** jet-classification thresholds, audibility slant thresholds and the tolerance sys allowances are provisional until real-data calibration (milestone 4.5). Don't tune them further against synthetic data (the synthetic generator uses the same audibility thresholds to place decoys, so synthetic validation is self-consistent whatever their values).
- **Clock offset:** absorbed by Δt in scoring; never trust file metadata blindly.
- **Harmonics:** the ridge can lock onto the wrong harmonic. f0 is free per event, so curve shape matters more than absolute frequency.
- **Coverage gaps:** aircraft without ADS-B, or with gaps in coverage, are missing from track data. This also weakens the silence constraint, so make the silence filter's strictness configurable and report how many cells it removes.
- **Type-code gaps:** aircraft missing from the type database get an "unknown" class, which matches either event class.
- **Single event:** one flyover constrains the mic to a locus (right slant distance and timing along one track), so ranking among locus cells is not meaningful. Location claims need at least two events from different tracks.
- **Grid quantization:** the 200 m grid is coarser than the score's resolution across the cost valley; always score cells by their best in-cell point (score.refine_in_cell).
- **Turns and climbs** break the straight-line closed forms. Scoring always uses the general track-based model.
- **Altitude source:** prefer geometric altitude; barometric altitude needs a pressure correction or a wider tolerance.
- **Geoid:** DEM heights are converted to ellipsoidal with a real geoid model sampled from a grid in data/proj/: GEOID18 (us_noaa_g2018u0.tif, CONUS + PR), EGM96 (us_nga_egm96_15.tif) outside it. The constant `dem.geoid_undulation_m` is only a fallback when no grid is found, and logs a warning. GEOID18 is a hybrid geoid referenced to NAD83(2011), whose ellipsoid heights differ from WGS84/ITRF by ~1–2 m in CONUS; negligible at 200 m spacing but not zero. Near Boulder: GEOID18 −16.10 m, EGM96 −15.58 m. If the grids are missing, re-copy them from QGIS.app/Contents/Resources/qgis/proj/ or run `.venv/bin/pyproj sync --file us_noaa_g2018u0 --target-directory data/proj` (and `--file us_nga_egm96_15`).
- **Dense TrackSet (US-wide scaling item, not fixed yet):** TrackSet stores (K, T, 3) float64 positions and velocities. 5,000 tracks × a 10-minute window ≈ 0.6 GB. Before the US-wide benchmark, chunk by time or store per-track ragged arrays.
- **Scale:** at US scale, memory and runtime blow up without spatial pruning. Tile the grid and process by tile.

## Non-goals (for now)
Multi-mic arrays, ML event classifiers, real-time processing, web UI, terrain acoustic modeling. Human-in-the-loop tooling until milestone 9.