# CLAUDE.md — Acoustic Doppler Geolocation

## Goal
Estimate where an audio recording was made using the aircraft audible in its background. Each flyover produces a Doppler curve in the spectrogram. Given ADS-B flight track data for a region and time window, find the microphone location (and, if needed, the recording's time offset) whose modeled Doppler curves best match the observed ones.

Outputs: a ranked list of candidate locations, a score raster over the search region, and diagnostics, all viewable in QGIS. A companion analysis assesses which common audio processing does and doesn't defeat the method.

## Core idea
A single flyover constrains the mic heavily:
- **t0** (closest-approach time, corrected for sound travel time) ties the mic to where the aircraft was at that moment.
- **d** (CPA slant distance) plus aircraft altitude above ground gives the horizontal distance from the track.
- **v** (speed from the Doppler shift) and spectral character (prop vs. jet) identify which aircraft it could be.

Two events from different tracks, plus their measured time offset and the absence of other expected flyovers (silence), narrow the location to a small set of grid cells. Full curve modeling and locked two-event scoring then rank those cells.

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
5. **Inter-event offset filter:** the received-time offset between events' CPAs is measured from the audio (e.g. 920 s in the validation case; never hardcode it). Keep only cells where some track pair produces the same received-time offset, including each cell's propagation delays, within tolerance.
6. **Silence constraint (required):** reject cells where any class-compatible track would produce an audible pass during the clip with no corresponding event in the audio. Audibility uses a configurable slant-distance threshold per class.
7. **Full curve modeling:** for surviving cells, model the exact Doppler curve each event would produce from interpolated ADS-B positions, using emission-time radial velocity and propagation delay.
8. **Locked two-event scoring:** score modeled curves against the observed ridges. Free parameters per cell:
   - one global time shift Δt, applied identically to both events (absorbs CPA-time measurement error and clock offset)
   - one f0 per event (absorbs source-frequency uncertainty)

   The relative timing between events comes only from the physics (track positions + propagation), and is never fitted independently per event. Score = residual between ridge and modeled curves; rank cells by combined score.
9. **Outputs:** ranked cells with assigned tracks per event, Δt, and per-event f0; score raster (GeoTIFF); candidate, track, and grid layers (GeoPackage); diagnostic plots with both modeled curves overlaid on the spectrogram for the top candidates; per-stage survivor counts.

### Search modes
- **Regional:** bounded region, known or windowed time.
- **US-wide:** a single clip of known time. The cascade (steps 4–6) must prune with vectorized and spatially indexed operations (e.g. only cells within audible range of tracks active at the event times) before step 7. Never run full curve modeling on the full grid. Always report cell counts surviving each stage.

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
cli.py                # subcommands: events, locate, synth, countermeasures
tests/
data/                 # gitignored: audio, tracks, DEMs, aircraft DB
outputs/              # gitignored
config.yaml           # search region, time prior, grid spacing, tolerances, bands, thresholds, temperature
```

## Conventions
- Python 3.11+: numpy, scipy, pandas, matplotlib, soundfile, pymap3d, pyproj, rasterio, geopandas, pyyaml. Tests with pytest. Use a venv and pin requirements.txt.
- Internal time is float seconds since Unix epoch (UTC). Convert only at I/O boundaries.
- Do distance and Doppler geometry in ECEF (EPSG:4978, metres): exact 3D slant ranges anywhere, no tile seams. The candidate grid is indexed in a projected CRS (UTM for regional, EPSG:5070 for US-wide); local ENU is only for plotting and synthetic scenario construction. Store and export in EPSG:4326.
- Heights: ADS-B geometric altitude is height above the WGS84 ellipsoid; DEMs are usually orthometric, so add the geoid undulation (config) before comparing them.
- Clip length varies from under a minute to hours: never assume quiet background frames exist, and window any per-clip arrays (TrackSet, spectrograms) so memory scales with the time span actually needed.
- SI units everywhere; put units in variable names when ambiguous (alt_m, speed_mps).
- Pure functions in doppler/*. Keep file I/O in cli.py, export.py, and plot.py.
- Type hints and short docstrings on public functions. Every module gets tests.
- Vectorize the cascade and scoring with numpy. Flag hot spots before reaching for numba or multiprocessing.
- All tolerances and thresholds live in config.yaml, not in code.
- Explain any new dependency before adding it.

## QGIS MCP (if connected)
- Use it for visualization and inspection only: loading outputs (score GeoTIFF, candidate, track, and grid GeoPackages) onto a satellite basemap, styling them, and checking results visually.
- Never put pipeline logic in QGIS. All computation (signal processing, fitting, grid generation, DEM sampling, filtering, scoring) lives in tested Python modules in doppler/. QGIS only reads files those modules export.
- Not needed before milestone 6. Don't set it up or call it earlier unless I ask.
- Avoid the arbitrary-code tool except for layer styling or loading tasks the standard tools can't do.

## Validation
- **Synthetic:** hidden mic, tracks of both classes, two events at a chosen offset, plus decoy tracks that must trigger the silence filter. The true cell must survive every cascade stage and rank 1st after scoring. Also report sensitivity to SNR, tolerance settings, and DEM error.
- **Real benchmark:** a 1-minute clip of known time, US-wide search. Success = the true location ranks 1st. Report the true location's rank and error distance, plus survivor counts per stage.

## Countermeasures analysis
Apply common audio processing to synthetic and real clips, rerun the full pipeline, and report:
- high-pass filtering
- masking with added noise or music ("drowning out")
- phone-call codec compression and noise reduction

For each: whether the true location still ranks 1st, how ridge SNR and fitted parameters change, and the physical reason it does or doesn't work (e.g. whether the Doppler *shape* and timing survive even when absolute level or low harmonics are removed).

## Milestones
1. Physics + tests. *(done in session 1)*
2. Synthetic audio + spectrogram + ridge + per-event fit. *(done in session 1)*
3. Tracks: ADS-B ingest, interpolation, type-code prop/jet classification, event classification, ENU conversion, DEM sampling.
4. Cascade: grid generation, CPA prefilter, offset filter, silence filter, with stage counts. The synthetic true cell survives.
5. Full curve modeling + locked two-event scoring. The synthetic true cell ranks 1st.
6. Exports + QGIS MCP visualization.
7. Real data: regional run, then the US-wide 1-minute benchmark.
8. Countermeasures analysis and report.

## Known pitfalls
- **Locked scoring:** per-event characterization fits t0 independently; scoring must not. Don't reuse independent t0s as scoring parameters.
- **Wind:** Doppler-derived v is relative to the air mass, so it can differ from ADS-B ground speed. Keep the v tolerance generous and configurable.
- **Clock offset:** absorbed by Δt in scoring; never trust file metadata blindly.
- **Harmonics:** the ridge can lock onto the wrong harmonic. f0 is free per event, so curve shape matters more than absolute frequency.
- **Coverage gaps:** aircraft without ADS-B, or with gaps in coverage, are missing from track data. This also weakens the silence constraint, so make the silence filter's strictness configurable and report how many cells it removes.
- **Type-code gaps:** aircraft missing from the type database get an "unknown" class, which matches either event class.
- **Turns and climbs** break the straight-line closed forms. Scoring always uses the general track-based model.
- **Altitude source:** prefer geometric altitude; barometric altitude needs a pressure correction or a wider tolerance.
- **Scale:** at US scale, memory and runtime blow up without spatial pruning. Tile the grid and process by tile.

## Non-goals (for now)
Multi-mic arrays, ML event classifiers, real-time processing, web UI, terrain acoustic modeling.