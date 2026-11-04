# HANDOFF — Acoustic Doppler Geolocation

Read CLAUDE.md first; it is authoritative for methodology and conventions. This file
records session state, decisions, and what is fragile.

## Status
- Milestones 1–3 done (tag `milestone-3` = e742f5c, created in session 3; it had not been tagged).
- Milestone 4 (cascade) in progress in session 3. See "Milestone 4" below for results once done.

## Decisions (session 3, approved by the user)
1. **Tolerances:** per event, `tol = √((n_σ · w_cov · σ)² + sys²)`. σ from the fit covariance;
   `w_cov` multiplies σ when the pass is edge-clipped (`coverage_tau` below threshold on a side);
   `sys` are absolute physical allowances (wind m/s, height m, model-error s). No fixed-percentage
   tolerances. Wind propagates into d (d = v·τ). Clock error lives only in the Δt prior. `sys` values
   are provisional until milestone 4.5.
2. **score.tif** is written in the grid's own projected CRS (UTM / EPSG:5070), not EPSG:4326,
   so raster cells align exactly with grid cells. Vectors stay in EPSG:4326.
3. **Parquet:** fastparquet + cramjam (small), not pyarrow. If a later dependency (e.g. pyopensky)
   pulls in pyarrow, switch to pyarrow then and drop fastparquet.
4. **plotly.min.js:** one-time download, pinned version recorded in config.yaml (`viewer.plotly_version`),
   stored once at outputs/plotly.min.js; `clean` never deletes it. No plotly Python package.
5. **Housekeeping:** old outputs/synth_* runs deleted (53 MB). User will run `pip cache purge` (609 MB).
6. **Synthetic audio:** 24-bit FLAC instead of float32 WAV.
7. **Geoid:** us_noaa_g2018u0.tif (GEOID18) and us_nga_egm96_15.tif (EGM96) copied from QGIS.app
   into data/proj/ (gitignored, 19 MB); pyproj is pointed there. Constant undulation is a fallback that
   logs a warning. NAD83(2011) vs WGS84 note is in CLAUDE.md Known pitfalls.
8. **N events:** cascade and score interfaces handle any number of events: one shared Δt, one f0 per
   event, relative timing from physics only. The offset filter is an interval intersection over events
   of the union over candidate passes (no combinatorics). Hypotheses forbid assigning the same track to
   two events unless `score.allow_shared_tracks: true`.
9. **Provisional thresholds:** jet-classification and audibility thresholds (and tolerance `sys`) are
   marked provisional in config.yaml; don't tune them against synthetic data. New milestone 4.5:
   real-data calibration.
10. **Before milestone 5:** `candidates` holds silence-stage survivors with null score/rank/f0; score.tif
    holds an interim cascade-consistency score, labelled interim in the band description and run.json.
11. **QGIS:** template built headlessly by qgis/build_project.py using QGIS.app's bundled Python (not the
    QGIS MCP). Starting point was the user's partial root doppler.qgz (EPSG:3857, WGS84 ellipsoid, meters,
    relative paths, Esri World Imagery; it actually had no layer groups, and duplicated Esri/Carto layers).
    Carto removed; Esri World Imagery + Esri Reference overlays (World_Boundaries_and_Places,
    World_Transportation). The user sets the QGIS network cache themself (it's an app-wide setting).
12. **Search regions** come from inputs/search_regions.gpkg (layer `search_regions`, EPSG:4326, field
    `name`), selected by `region.name` in config.yaml.
13. **DEM access** is windowed per grid tile (rasterio windowed reads).

## Fragile / watch
- Dense TrackSet memory at US scale (CLAUDE.md Known pitfalls); not fixed yet.
- The per-event fit is a straight-line model: on curved tracks its σ is optimistic, which is what the
  `sys` model-error allowance and `w_cov` cover.
