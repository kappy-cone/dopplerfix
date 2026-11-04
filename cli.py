"""Command line: synth | events | tracks | locate.

    python cli.py synth  --config config.yaml --out outputs/synth_seed0 [--seed 0] [--event-times 150,420]
    python cli.py events --config config.yaml --audio outputs/synth_seed0/audio.wav [--out events.json]
    python cli.py tracks --config config.yaml --tracks outputs/synth_seed0/tracks.csv --registry .../aircraft_db.csv
    python cli.py locate ...   (milestones 4-5)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml

from doppler import aircraft_types, audio, classify, detect, fit, physics, ridge, synth, tracks
from doppler.geo import ArrayDEM, FlatDEM, GeoidModel, LocalFrame


def load_config(path: str | Path) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def speed_of_sound_from(cfg: dict) -> float:
    t = (cfg.get("environment") or {}).get("temperature_c")
    return physics.speed_of_sound(t) if t is not None else physics.DEFAULT_C


def section(cfg: dict, name: str) -> dict:
    return dict(cfg.get(name) or {})


def event_params(cfg: dict) -> dict:
    """All per-event processing parameter objects from config."""
    rp = section(cfg, "ridge")
    return dict(
        spec=audio.SpectrogramParams.from_dict(section(cfg, "spectrogram")),
        spec_jet=audio.SpectrogramParams.from_dict(section(cfg, "spectrogram_jet")) if cfg.get("spectrogram_jet") else None,
        detect=detect.DetectParams.from_dict(section(cfg, "detect")),
        ridge=ridge.RidgeParams.from_dict(rp),
        ridge_jet=ridge.RidgeParams.from_dict({**rp, **section(cfg, "ridge_jet")}),
        fit=fit.FitParams.from_dict(section(cfg, "fit")),
        classify=classify.ClassifyParams.from_dict(section(cfg, "classify")),
    )


def run_events(x, fs, cfg: dict, c: float):
    """Chunked detection -> per-window ridges + fits -> prop/jet labels."""
    p = event_params(cfg)
    det = detect.detect_events_audio(x, fs, p["spec"], p["detect"], p["classify"].broadband_band_hz, p["spec_jet"])
    fits = fit.characterize_audio(x, fs, det, p["spec"], p["ridge"], p["ridge_jet"], p["fit"], p["classify"], c,
                                  jet_spec_params=p["spec_jet"])
    return det, fits


def print_fits(fits) -> None:
    print(f"{'#':>2} {'t_cpa_rx':>9} {'f0 Hz':>7} {'v m/s':>12} {'d m':>12} {'t0 s':>14} {'rms':>5} class  cover")
    for i, e in enumerate(fits):
        s = e.sigma
        cov = "/".join(f"{x:.1f}" for x in e.coverage)
        print(f"{i:2d} {e.t_cpa_received_s:9.2f} {e.f0_hz:7.1f} {e.v_mps:6.1f}±{s['v_mps']:<5.1f}"
              f" {e.d_m:6.0f}±{s['d_m']:<5.0f} {e.t0_s:8.2f}±{s['t0_s']:<5.2f} {e.rms_hz:5.2f}"
              f" {aircraft_types.CLASS_NAMES[e.cls]:7s} {cov}")


# ---------------------------------------------------------------------------
# Geoid, DEM, regions (file I/O lives here, not in doppler/*)
# ---------------------------------------------------------------------------
def setup_geoid(cfg: dict) -> GeoidModel:
    """Put the geoid grid directory on pyproj's data path and build the geoid model (fallback: constant)."""
    import pyproj

    g = section(cfg, "geoid")
    d = g.get("grid_dir")
    if d and Path(d).is_dir():
        pyproj.datadir.append_data_dir(str(Path(d).resolve()))
    return GeoidModel(g.get("grids") or [], section(cfg, "dem").get("geoid_undulation_m", 0.0))


class RasterDEMSource:
    """Windowed reads of an orthometric DEM raster/VRT; returns an ellipsoidal ArrayDEM per tile."""

    def __init__(self, path: str | Path, geoid: GeoidModel):
        import rasterio
        from pyproj import Transformer

        self.ds = rasterio.open(path)
        self.geoid = geoid
        self._to = Transformer.from_crs("EPSG:4326", self.ds.crs, always_xy=True)
        self.reads = 0

    def for_bounds(self, lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> ArrayDEM:
        from rasterio.windows import Window, from_bounds

        xs, ys = self._to.transform([lon_min, lon_max, lon_min, lon_max], [lat_min, lat_min, lat_max, lat_max])
        w = from_bounds(min(xs), min(ys), max(xs), max(ys), self.ds.transform)
        c0, r0 = int(np.floor(w.col_off)) - 2, int(np.floor(w.row_off)) - 2
        win = Window(c0, r0, int(np.ceil(w.width)) + 5, int(np.ceil(w.height)) + 5)
        nod = self.ds.nodata
        arr = self.ds.read(1, window=win, boundless=True, fill_value=nod if nod is not None else np.nan,
                           out_dtype="float64")
        self.reads += 1
        return ArrayDEM(arr, self.ds.window_transform(win), self.ds.crs.to_string(), nod, self.geoid)


def load_dem_source(cfg: dict, geoid: GeoidModel):
    """Flat ellipsoidal DEM (synthetic scenarios), or windowed reads of the configured raster/VRT."""
    d = section(cfg, "dem")
    if not d.get("path"):
        return FlatDEM(section(cfg, "region").get("ground_elev_m", 0.0))
    return RasterDEMSource(d["path"], geoid)


REGION_LAYER = "search_regions"


def load_region(cfg: dict, name: str | None = None):
    """(name, EPSG:4326 polygon) for a named search region from inputs/search_regions.gpkg."""
    import pyogrio

    r = section(cfg, "region")
    name = name or r.get("name")
    path = r.get("regions_path", "inputs/search_regions.gpkg")
    gdf = pyogrio.read_dataframe(path, layer=REGION_LAYER)
    sel = gdf[gdf["name"] == name]
    if sel.empty:
        raise SystemExit(f"search region {name!r} not in {path} (have: {sorted(gdf['name'])})")
    return name, sel.to_crs("EPSG:4326").geometry.union_all()


def region_frame(region_lonlat) -> tuple[LocalFrame, float]:
    """ENU frame at the region's centroid and its equal-area radius (synthetic scenarios, plots)."""
    from pyproj import Transformer
    import shapely

    from doppler.geo import grid_crs

    crs = grid_crs(region_lonlat, "regional")
    fwd = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    inv = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    poly = shapely.transform(region_lonlat, lambda xy: np.c_[fwd.transform(xy[:, 0], xy[:, 1])])
    lon, lat = inv.transform(poly.centroid.x, poly.centroid.y)
    return LocalFrame(float(lat), float(lon), 0.0), float(np.sqrt(poly.area / np.pi))


def circle_region(lat: float, lon: float, radius_m: float, n: int = 128):
    import shapely

    fr = LocalFrame(lat, lon, 0.0)
    a = np.linspace(0, 2 * np.pi, n + 1)[:-1]
    la, lo, _ = fr.to_geodetic(np.c_[radius_m * np.sin(a), radius_m * np.cos(a), np.zeros(n)])
    return shapely.Polygon(np.c_[lo, la])


def read_track_table(path: str | Path) -> pd.DataFrame:
    from doppler import export

    return export.read_parquet(path) if str(path).endswith(".parquet") else pd.read_csv(path)


def classify_tracks(cfg: dict, prepared: list[tracks.Track], registry: pd.DataFrame | str | None) -> None:
    """Attach type codes and prop/jet/unknown classes from the aircraft registry (in place)."""
    at_cfg = section(cfg, "aircraft_types")
    if isinstance(registry, (str, Path)) or registry is None:
        reg_path = registry or at_cfg.get("registry_path")
        if not (reg_path and Path(reg_path).exists()):
            return
        registry = pd.read_csv(reg_path, dtype=str)
    tt = pd.read_csv(at_cfg["type_table_path"]) if at_cfg.get("type_table_path") else None
    lookup = aircraft_types.build_lookup(registry, tt)
    types, cls = aircraft_types.classify_icao24([t.icao24 for t in prepared], lookup)
    for t, ty, cl in zip(prepared, types, cls):
        t.type_code, t.cls = str(ty), int(cl)


def load_tracks(cfg: dict, tracks_path: str, registry_path: str | None,
                t_range_s: tuple[float, float] | None = None) -> list[tracks.Track]:
    """Read ADS-B (CSV/Parquet), crop to the needed time window, clean + resample, classify."""
    tp = section(cfg, "tracks")
    df = read_track_table(tracks_path)
    params = tracks.TrackParams.from_dict(tp)
    if t_range_s is not None:
        df = tracks.normalize_frame(df, tp.get("columns") or None)
        pad = params.max_gap_s
        df = df[(df["t_s"] >= t_range_s[0] - pad) & (df["t_s"] <= t_range_s[1] + pad)]
    raw = tracks.tracks_from_frame(df, params, tp.get("columns") or None)
    prepared = tracks.prepare_tracks(raw, params)
    classify_tracks(cfg, prepared, registry_path)
    return prepared


# ---------------------------------------------------------------------------
# Cascade orchestration (shared by locate and synth --cascade-decoys)
# ---------------------------------------------------------------------------
def cascade_inputs(cfg: dict, fits, prepared: list[tracks.Track], tprior, duration_s: float, c: float,
                   region_lonlat):
    """ObservedEvents, Δt prior, windowed TrackSet, grid spec and cascade params."""
    from doppler import cascade, geo

    cp_cfg = {**section(cfg, "cascade"), **{k: v for k, v in section(cfg, "score").items()
                                            if k in ("allow_shared_tracks", "max_hypotheses_per_cell")}}
    cp = cascade.CascadeParams.from_dict(cp_cfg)
    clock = float(section(cfg, "time_prior").get("clock_tol_s", 10.0))
    span = (min(t.t_s[0] for t in prepared), max(t.t_s[-1] for t in prepared)) if prepared else None
    prior = cascade.dt_prior_from(tprior.mode, tprior.start_s, tprior.window_s, clock, duration_s, span)
    events = cascade.observed_events(fits, c)
    reach = max([cp.threshold(k) for k in range(3)] + [e.d_m * 2 + 1000 for e in events])
    pad = 60.0
    t_lo = prior.start_s + prior.dt_lo_s - reach / c - pad
    t_hi = prior.start_s + prior.dt_hi_s + duration_s + pad
    dt = section(cfg, "tracks").get("resample_s", 0.25)
    ts = tracks.build_trackset(prepared, t_lo, t_hi, dt)
    g = section(cfg, "grid")
    mode = section(cfg, "search").get("mode", "regional")
    spec = geo.make_grid_spec(region_lonlat, g.get("spacing_m", 200), cp.tile_cells, mode, g.get("crs", "auto"))
    return events, prior, ts, spec, cp


def offset_survivors_enu(cfg: dict, fits, prepared, tprior, duration_s, c, region_lonlat, frame: LocalFrame,
                         dem_source, mic_h: float) -> np.ndarray:
    """ENU (E, N) of cells surviving the CPA and offset stages (used to target synthetic decoys)."""
    from doppler import cascade

    events, prior, ts, spec, cp = cascade_inputs(cfg, fits, prepared, tprior, duration_s, c, region_lonlat)
    pts = []
    for r in cascade.iter_cascade(spec, events, ts, prior, dem_source, mic_h, c, cp):
        if r.offset.any():
            pts.append(frame.ecef_to_enu(r.tile.xyz[r.offset])[:, :2])
    return np.concatenate(pts) if pts else np.zeros((0, 2))


def choose_decoy_targets(cands_enu: np.ndarray, mic_enu: np.ndarray, p: synth.SynthParams, n: int,
                         min_sep_m: float = 400.0) -> list[tuple[float, float]]:
    """Up to n feasible targets, farthest from the mic first, at least min_sep_m apart."""
    if n <= 0 or len(cands_enu) == 0:
        return []
    ok = np.array([synth.decoy_target_feasible(p, x, mic_enu, "prop") for x in cands_enu])
    pts = cands_enu[ok]
    order = np.argsort(-np.hypot(*(pts - mic_enu[:2]).T))
    chosen: list[np.ndarray] = []
    for k in order:
        if all(np.hypot(*(pts[k] - q)) >= min_sep_m for q in chosen):
            chosen.append(pts[k])
        if len(chosen) >= n:
            break
    return [(float(x), float(y)) for x, y in chosen]


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def synth_params_from(cfg: dict, args, radius_m: float) -> synth.SynthParams:
    sp = dict(cfg.get("synth") or {})
    for k in ("seed", "snr_db", "n_tracks", "duration_s"):
        if getattr(args, k, None) is not None:
            sp[k] = getattr(args, k)
    if getattr(args, "event_times", None):
        sp["event_rx_times_s"] = [float(v) for v in args.event_times.split(",")]
        sp.setdefault("event_classes", None)
        if args.event_classes:
            sp["event_classes"] = args.event_classes.split(",")
        elif sp.get("event_classes") and len(sp["event_classes"]) != len(sp["event_rx_times_s"]):
            sp["event_classes"] = None
    elif getattr(args, "n_tracks", None) is not None:
        sp["event_rx_times_s"] = None  # legacy even spread
    sp.setdefault("audible_slant_m", section(cfg, "cascade").get("audible_slant_m"))
    sp.setdefault("region_radius_m", radius_m)
    if sp.get("audible_slant_m") is None:
        sp.pop("audible_slant_m")
    mic_cfg = cfg.get("mic") or {}
    if "height_agl_m" in mic_cfg:
        sp.setdefault("mic_height_m", mic_cfg["height_agl_m"])
    return synth.SynthParams.from_dict(sp)


def cmd_synth(args) -> int:
    from doppler import export, plot

    cfg = load_config(args.config)
    _, region = load_region(cfg, args.region)
    frame0, radius = region_frame(region)
    params = synth_params_from(cfg, args, radius)
    tp = audio.parse_time_prior(cfg.get("time_prior") or {})
    if tp.start_s is None:
        print("synth needs time_prior.mode: known with start_utc (the scenario's true start)", file=sys.stderr)
        return 2
    c = speed_of_sound_from(cfg)
    ground = section(cfg, "region").get("ground_elev_m", 0.0)
    frame = LocalFrame(frame0.lat0, frame0.lon0, ground)
    from doppler import realism as rl

    rp = rl.RealismParams() if args.clean else rl.RealismParams.from_dict(section(cfg, "realism"))
    sc = synth.make_scenario(params, frame, tp.start_s, ground, c, realism=rp)
    n_dec = params.n_cascade_decoys if args.cascade_decoys is None else args.cascade_decoys
    targets = []
    if n_dec > 0:
        # Decoys over cells that survive CPA + offset (far enough from the mic): only the silence filter can
        # remove those cells, so they test it. Built with a separate random stream; events are unchanged.
        _, fits0 = run_events(sc.audio, sc.fs_hz, cfg, c)
        tp_ = tracks.TrackParams.from_dict(section(cfg, "tracks"))
        src = sc.adsb_frame if sc.adsb_frame is not None else None
        prep = (tracks.prepare_tracks(tracks.tracks_from_frame(src, tp_), tp_) if src is not None
                else tracks.prepare_tracks(sc.tracks, tp_))
        classify_tracks(cfg, prep, sc.registry)
        cands = offset_survivors_enu(cfg, fits0, prep, tp, params.duration_s, c, region, frame, FlatDEM(ground),
                                     params.mic_height_m)
        targets = choose_decoy_targets(cands, sc.mic_enu, params, n_dec)
        if targets:
            sc = synth.make_scenario(params, frame, tp.start_s, ground, c, mic_enu=sc.mic_enu,
                                     decoy_targets_enu=targets, realism=rp)
        print(f"cascade decoys: {len(targets)} of {n_dec} placed over CPA/offset survivors "
              f"({len(cands)} survivors, feasible need ≥ "
              f"{(params.inaudible_margin - params.decoy_target_slant_frac) * params.threshold('prop'):.0f} m from mic)")

    out = Path(args.out or f"outputs/scenarios/synth_seed{params.seed}")
    paths = export.write_scenario(sc, out)
    det, fits = run_events(sc.audio, sc.fs_hz, cfg, c)
    disp = audio.compute_spectrogram(sc.audio, sc.fs_hz, audio.SpectrogramParams(8000, 1.0, 0.5, 3900.0))
    plot.plot_scenario(sc, disp, fits, out / "diagnostic.png")

    print(f"hidden mic: {sc.mic_lat:.6f}, {sc.mic_lon:.6f}  (ENU {sc.mic_enu[0]:.0f} E, {sc.mic_enu[1]:.0f} N)")
    print(f"c = {c:.1f} m/s, {len(sc.truth)} flyovers, SNR {params.snr_db:g} dB, clip {params.duration_s:g} s")
    for t, s_ in zip(sc.truth, sc.event_specs):
        print(f"  truth {s_.callsign} {t.icao24} ({s_.cls}): rx CPA {t.t_cpa_received_rel_s:7.2f}s  v {t.v_mps:5.1f}"
              f"  d {t.d_m:6.0f}  f0 {t.f0_hz:6.1f}  turn {s_.turn_rate_deg_s:+.2f}°/s")
    roles = pd.Series([s_.role for s_ in sc.specs]).value_counts().to_dict()
    failed = sum(1 for d in sc.decoys if not d.icao24)
    print(f"tracks by role: {roles}" + (f"  ({failed} non-event placements infeasible)" if failed else ""))
    print("fitted events:")
    print_fits(fits)
    for k, p_ in {**paths, "plot": out / "diagnostic.png"}.items():
        print(f"  wrote {k:8s} {p_}")
    return 0


def cmd_events(args) -> int:
    from doppler import export

    cfg = load_config(args.config)
    x, fs = sf.read(args.audio, always_2d=False)
    sidecar = Path(args.sidecar) if args.sidecar else Path(args.audio).with_suffix(".json")
    tp_dict = json.loads(sidecar.read_text()) if sidecar.exists() else (cfg.get("time_prior") or {})
    tp = audio.parse_time_prior(tp_dict)
    c = speed_of_sound_from(cfg)
    det, fits = run_events(audio.mix_to_mono(x), fs, cfg, c)
    print(f"time prior: {tp.mode}; clip {len(x) / fs:.1f} s; {len(det.windows)} windows, {len(fits)} events")
    print_fits(fits)
    if args.out:
        export.write_events(fits, Path(args.out), tp.start_s)
        print(f"wrote {args.out}")
    return 0


def cmd_tracks(args) -> int:
    cfg = load_config(args.config)
    prepared = load_tracks(cfg, args.tracks, args.registry)
    cls = pd.Series([aircraft_types.CLASS_NAMES[t.cls] for t in prepared]).value_counts().to_dict()
    alt = pd.Series([t.alt_source for t in prepared]).value_counts().to_dict()
    n_air = len({t.icao24 for t in prepared})
    span = (min(t.t_s[0] for t in prepared), max(t.t_s[-1] for t in prepared)) if prepared else (np.nan, np.nan)
    print(f"{n_air} aircraft, {len(prepared)} gap-free segments, classes {cls}, altitude source {alt}")
    print(f"time span {pd.Timestamp(span[0], unit='s', tz='UTC')} .. {pd.Timestamp(span[1], unit='s', tz='UTC')}")
    return 0


def cmd_regions(args) -> int:
    import geopandas as gpd
    import pyogrio

    cfg = load_config(args.config)
    path = Path(args.path or section(cfg, "region").get("regions_path", "inputs/search_regions.gpkg"))
    gdf = pyogrio.read_dataframe(path, layer=REGION_LAYER) if path.exists() else \
        gpd.GeoDataFrame({"name": []}, geometry=[], crs="EPSG:4326")
    if args.action == "add":
        if (gdf["name"] == args.name).any() and not args.replace:
            print(f"region {args.name!r} exists (use --replace)", file=sys.stderr)
            return 2
        poly = circle_region(args.lat, args.lon, args.radius_m)
        gdf = gdf[gdf["name"] != args.name]
        new = gpd.GeoDataFrame({"name": [args.name]}, geometry=[poly], crs="EPSG:4326")
        gdf = pd.concat([gdf.to_crs("EPSG:4326"), new], ignore_index=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        pyogrio.write_dataframe(gpd.GeoDataFrame(gdf, geometry="geometry", crs="EPSG:4326"), path,
                                layer=REGION_LAYER, driver="GPKG")
        print(f"wrote {args.name!r} to {path}")
    for _, row in gdf.iterrows():
        fr, rad = region_frame(row.geometry)
        print(f"  {row['name']}: centroid {fr.lat0:.5f}, {fr.lon0:.5f}; equal-area radius {rad:.0f} m")
    return 0


def _git_state() -> dict:
    import subprocess

    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
        return {"commit": head, "dirty": dirty}
    except Exception:
        return {"commit": None, "dirty": None}


def _cell_frame(spec, tile, rows, to_geo, **cols):
    import geopandas as gpd
    from doppler.geo import cell_polygons_lonlat

    geom = cell_polygons_lonlat(spec, tile.i[rows], tile.j[rows], to_geo)
    data = {"cell_id": spec.cell_ids(tile.i[rows], tile.j[rows]), "i": tile.i[rows].astype(np.int32),
            "j": tile.j[rows].astype(np.int32), "ground_h_m": tile.ground_h_m[rows].astype(np.float32)}
    data.update({k: (v[rows] if isinstance(v, np.ndarray) and v.shape[:1] == (len(tile),) else v)
                 for k, v in cols.items()})
    return gpd.GeoDataFrame(data, geometry=geom, crs="EPSG:4326")


def manual_inputs(cfg: dict, args) -> dict:
    """Per-clip manual inputs that would make a run not fully automated (CLAUDE.md Operating modes)."""
    m = {}
    if section(cfg, "detect").get("manual_windows"):
        m["detect.manual_windows"] = section(cfg, "detect")["manual_windows"]
    return m


def cmd_locate(args) -> int:
    """Cascade run (milestone 4) with the full outputs contract; scoring placeholders until milestone 5."""
    import time as _time

    import geopandas as gpd
    from shapely.geometry import LineString, Point

    from doppler import cascade, export, geo, plot, score

    T0 = _time.perf_counter()
    timings: dict[str, float] = {}
    cfg = load_config(args.config)
    oc = section(cfg, "outputs")
    root = Path(oc.get("root", "outputs"))
    sc_dir = Path(args.scenario) if args.scenario else None
    audio_path = args.audio or (sc_dir and next((str(sc_dir / n) for n in ("audio.flac", "audio.wav")
                                                  if (sc_dir / n).exists()), None))
    tracks_path = args.tracks or (sc_dir and str(sc_dir / "tracks.parquet"))
    registry = args.registry or (sc_dir and (sc_dir / "aircraft_db.csv").exists() and str(sc_dir / "aircraft_db.csv")) or None
    truth_path = args.truth or (sc_dir and (sc_dir / "truth.json").exists() and str(sc_dir / "truth.json")) or None
    if not audio_path or not tracks_path:
        print("locate needs --scenario, or --audio and --tracks", file=sys.stderr)
        return 2

    geoid = setup_geoid(cfg)
    dem_source = load_dem_source(cfg, geoid)
    region_name, region = load_region(cfg, args.region)
    c = speed_of_sound_from(cfg)
    mic_h = float(section(cfg, "mic").get("height_agl_m", 1.5))
    mode = section(cfg, "search").get("mode", "regional")

    # Audio -> events
    t = _time.perf_counter()
    x, fs = sf.read(audio_path, always_2d=False)
    x = audio.mix_to_mono(x)
    duration = len(x) / fs
    sidecar = Path(args.sidecar) if args.sidecar else Path(audio_path).with_suffix(".json")
    tprior = audio.parse_time_prior(json.loads(sidecar.read_text()) if sidecar.exists() else section(cfg, "time_prior"))
    _, fits = run_events(x, fs, cfg, c)
    timings["events_s"] = _time.perf_counter() - t
    if not fits:
        print("no events detected", file=sys.stderr)
        return 1

    # Tracks (cropped to the window the cascade can use)
    t = _time.perf_counter()
    clock = float(section(cfg, "time_prior").get("clock_tol_s", 10.0))
    if tprior.mode == "known":
        win = (tprior.start_s - clock - 120.0, tprior.start_s + clock + duration + 60.0)
    elif tprior.mode == "window":
        win = (tprior.window_s[0] - clock - 120.0, tprior.window_s[1] + clock + duration + 60.0)
    else:
        win = None
    prepared = load_tracks(cfg, tracks_path, registry, win)
    events, prior, ts, spec, cp = cascade_inputs(cfg, fits, prepared, tprior, duration, c, region)
    timings["tracks_s"] = _time.perf_counter() - t

    run_dir = export.new_run_dir(root, args.run_id, tag=(sc_dir.name if sc_dir else ""))
    run_id = run_dir.name
    write_grid = args.write_grid if args.write_grid is not None else \
        {"auto": mode == "regional", True: True, False: False}.get(oc.get("write_grid", "auto"), mode == "regional")

    # Truth (synthetic): the cell containing the true mic
    truth = json.loads(Path(truth_path).read_text()) if truth_path else None
    true_ij = None
    if truth:
        from pyproj import Transformer

        tx, ty = Transformer.from_crs("EPSG:4326", spec.crs, always_xy=True).transform(truth["mic"]["lon"], truth["mic"]["lat"])
        true_ij = (int(np.floor(tx / spec.spacing_m)), int(np.floor(ty / spec.spacing_m)))
    true_stage = "outside region/grid"

    # Cascade, tile by tile
    t = _time.perf_counter()
    setup = cascade.prepare_cascade(spec, events, ts, prior, c, cp)
    timings["cascade_setup_s"] = _time.perf_counter() - t
    gpkg = run_dir / "results.gpkg"
    buf = export.LayerBuffer(gpkg)
    to_geo = geo.tiles_to_geodetic(spec)
    counts = {k: 0 for k in cascade.STAGES}
    stage_s = {k: 0.0 for k in cascade.STAGES}
    surv = []           # per silence survivor: dict
    offset_costs = []
    culprits = set()
    n_tiles_run = 0
    for r in cascade.iter_cascade(spec, events, ts, prior, dem_source, mic_h, c, cp, setup=setup,
                                  all_tiles=write_grid):
        n_tiles_run += 1
        tile = r.tile
        M = len(tile)
        for k_, v_ in r.seconds.items():
            stage_s[k_] += v_
        counts["grid"] += M
        counts["cpa"] += int(r.cpa.sum())
        counts["offset"] += int(r.offset.sum())
        counts["silence"] += int(r.silence.sum())
        if true_ij is not None and M:
            hit = np.flatnonzero((tile.i == true_ij[0]) & (tile.j == true_ij[1]))
            if len(hit):
                h = hit[0]
                true_stage = ("silence" if r.silence[h] else "offset" if r.offset[h] else "cpa" if r.cpa[h] else "grid")
        if M == 0:
            continue
        cost = np.full(M, np.nan)
        has = r.best >= 0
        cost[has] = r.hypotheses.cost[r.best[has]]
        npass = np.bincount(r.passes.cell, minlength=M) if len(r.passes) else np.zeros(M, int)
        stage_code = r.cpa.astype(np.int8) + r.offset + r.silence
        if write_grid:
            buf.add("grid", _cell_frame(spec, tile, np.arange(M), to_geo, stage_reached=stage_code))
        common = dict(n_passes=npass.astype(np.int32), interim_cost=cost.astype(np.float32), dt_s=r.dt_s.astype(np.float32),
                      n_audible=r.n_audible.astype(np.int32), n_unexplained=r.n_unexplained.astype(np.int32))
        for name, mask in (("stage_cpa", r.cpa), ("stage_offset", r.offset), ("stage_silence", r.silence)):
            rows = np.flatnonzero(mask)
            if len(rows):
                buf.add(name, _cell_frame(spec, tile, rows, to_geo, **common))
        offset_costs.append(cost[r.offset])
        culprits.update(int(k_) for k_ in r.culprit_track)
        for h in np.flatnonzero(r.silence):
            b = r.best[h]
            hp = r.hypotheses.passes[b]
            n_h = int((r.hypotheses.cell == h).sum())
            surv.append({
                "cell_id": int(spec.cell_ids(tile.i[h], tile.j[h])), "i": int(tile.i[h]), "j": int(tile.j[h]),
                "lat": float(tile.lat[h]), "lon": float(tile.lon[h]), "ground_h_m": float(tile.ground_h_m[h]),
                "xyz": tile.xyz[h].copy(), "interim_cost": float(cost[h]), "dt_s": float(r.dt_s[h]),
                "dt_lo_s": float(r.hypotheses.dt_lo_s[b]), "dt_hi_s": float(r.hypotheses.dt_hi_s[b]),
                "tracks": [int(k_) for k_ in r.hypotheses.tracks[b]], "n_hypotheses": n_h,
                "n_audible": int(r.n_audible[h]), "sil_pen": float(r.silence_penalty[h]),
                "hyps": [(cascade.Hypothesis(int(spec.cell_ids(tile.i[h], tile.j[h])),
                                             tuple(int(k_) for k_ in r.hypotheses.tracks[q_]),
                                             (float(r.hypotheses.dt_lo_s[q_]), float(r.hypotheses.dt_hi_s[q_]))),
                          [None if q < 0 else
                           {"t_emit_s": float(r.passes.t_emit_s[q]), "r_m": float(r.passes.r_cpa_m[q]),
                            "speed_mps": float(r.passes.speed_mps[q]), "pos": r.passes.pos[q].copy()}
                           for q in r.hypotheses.passes[q_]])
                         for q_ in np.flatnonzero(r.hypotheses.cell == h)],
            })
    timings.update({f"cascade_{k}_s": v for k, v in stage_s.items()})
    grid_exact = True
    if not write_grid:  # tiles that were never built still count towards the grid stage
        counts["grid"], grid_exact = geo.count_grid_cells(spec)

    # Interim cascade ranking (kept for comparison), then locked N-event scoring of every hypothesis
    surv.sort(key=lambda d: d["interim_cost"])
    for k_, d in enumerate(surv, 1):
        d["interim_rank"] = k_
    t = _time.perf_counter()
    sp = score.ScoreParams.from_dict(section(cfg, "score"))
    by_id = {d["cell_id"]: d for d in surv}
    ranked = score.rank_cells(events, [(d["cell_id"], d["xyz"], [h for h, _ in d["hyps"]], d["sil_pen"])
                                       for d in surv], ts,
                              prior.start_s, c, sp, (prior.dt_lo_s, prior.dt_hi_s), 0.5 * spec.spacing_m)
    n_scored = sum(len(d["hyps"]) for d in surv)
    for r in ranked:
        d = by_id[r.cell_id]
        h, cp_ = d["hyps"][r.hyp_index]
        sc = r.best
        la, lo_, _ = geo.ecef_to_geodetic(r.xyz_est)
        cpa = ([None if k_ < 0 else score.cpa_geometry(r.xyz_est, k_, ts, cp0["t_emit_s"])
                for k_, cp0 in zip(sc.assignment, cp_)] if r.refined else cp_)
        d.update(score=sc.cost, score_centre=r.score_centre, dt_s=sc.dt_s, dt_lo_s=h.dt_bounds_s[0],
                 dt_hi_s=h.dt_bounds_s[1], tracks=list(sc.assignment), cpa=cpa, f0=sc.f0_hz, rms=sc.rms_hz,
                 n_missing=sc.n_missing, est_lat=float(la), est_lon=float(lo_),
                 est_offset_m=float(np.hypot(*r.offset_en_m)), refined=r.refined, xyz_est=r.xyz_est)
    surv.sort(key=lambda d: (sum(k_ < 0 for k_ in d["tracks"]), d["score"]))
    for k_, d in enumerate(surv, 1):
        d["rank"] = k_
    amb = score.ambiguity([d["score"] for d in surv], [d["xyz_est"] for d in surv], sp,
                          [sum(k_ < 0 for k_ in d["tracks"]) for d in surv])
    tied_ids = {surv[k_]["cell_id"] for k_ in amb["tied"]}
    timings["score_s"] = _time.perf_counter() - t
    timings["n_hypotheses_scored"] = n_scored
    t = _time.perf_counter()
    N = len(events)
    tc = {tr.icao24: tr.type_code for tr in prepared}
    cand = {"cell_id": [d["cell_id"] for d in surv], "rank": np.array([d["rank"] for d in surv], np.int32),
            "score": np.array([d["score"] for d in surv], np.float32),
            "interim_rank": np.array([d["interim_rank"] for d in surv], np.int32),
            "interim_cost": np.array([d["interim_cost"] for d in surv], np.float32),
            "dt_s": [d["dt_s"] for d in surv], "dt_lo_s": [d["dt_lo_s"] for d in surv],
            "dt_hi_s": [d["dt_hi_s"] for d in surv], "n_hypotheses": [d["n_hypotheses"] for d in surv],
            "n_audible": [d["n_audible"] for d in surv], "lat": [d["lat"] for d in surv],
            "lon": [d["lon"] for d in surv], "ground_h_m": [d["ground_h_m"] for d in surv],
            "score_centre": np.array([d["score_centre"] for d in surv], np.float32),
            "est_lat": [d["est_lat"] for d in surv], "est_lon": [d["est_lon"] for d in surv],
            "est_offset_m": np.array([d["est_offset_m"] for d in surv], np.float32),
            "refined": [d["refined"] for d in surv],
            "tied_with_rank1": [d["cell_id"] in tied_ids for d in surv]}
    for e in range(N):
        cand[f"f0_e{e}_hz"] = np.array([d["f0"][e] for d in surv], np.float32)
        cand[f"rms_e{e}_hz"] = np.array([d["rms"][e] for d in surv], np.float32)
        cand[f"track_e{e}"] = [str(ts.icao24[d["tracks"][e]]) if d["tracks"][e] >= 0 else "" for d in surv]
        cand[f"callsign_e{e}"] = [str(ts.callsign[d["tracks"][e]]) if d["tracks"][e] >= 0 else "" for d in surv]
    cand["n_unmatched"] = [sum(k_ < 0 for k_ in d["tracks"]) for d in surv]
    cand["silence_nats"] = np.array([d["sil_pen"] for d in surv], np.float32)
    buf.add("candidates", gpd.GeoDataFrame(cand, geometry=[Point(d["est_lon"], d["est_lat"]) for d in surv],
                                           crs="EPSG:4326"))

    pts, prow = [], {"cell_id": [], "rank": [], "event": [], "icao24": [], "callsign": [], "t_emit_utc": [],
                     "r_cpa_m": [], "alt_m": []}
    for d in surv:
        for e, (k_, cp_) in enumerate(zip(d["tracks"], d["cpa"])):
            if k_ < 0 or cp_ is None:
                continue
            la, lo, hh = geo.ecef_to_geodetic(cp_["pos"])
            pts.append(Point(float(lo), float(la)))
            for key, val in (("cell_id", d["cell_id"]), ("rank", d["rank"]), ("event", e),
                             ("icao24", str(ts.icao24[k_])), ("callsign", str(ts.callsign[k_])),
                             ("t_emit_utc", pd.Timestamp(cp_["t_emit_s"], unit="s", tz="UTC").isoformat()),
                             ("r_cpa_m", cp_["r_m"]), ("alt_m", float(hh))):
                prow[key].append(val)
    buf.add("event_cpa_points", gpd.GeoDataFrame(prow, geometry=pts, crs="EPSG:4326"))

    assigned = {k_ for d in surv for k_ in d["tracks"] if k_ >= 0}
    ktracks = sorted(assigned | culprits)
    lines, trow = [], {"icao24": [], "callsign": [], "class": [], "type_code": [], "alt_source": [], "role": [],
                       "n_candidates": []}
    n_assigned = {k_: sum(k_ in d["tracks"] for d in surv) for k_ in assigned}
    step = max(1, int(round(2.0 / ts.dt_s)))
    for k_ in ktracks:
        v = ts.valid()[k_]
        xyz = ts.xyz[k_][v][::step]
        if len(xyz) < 2:
            continue
        la, lo, _ = geo.ecef_to_geodetic(xyz)
        lines.append(LineString(np.c_[lo, la]))
        role = "+".join([r_ for r_, ok in (("assigned", k_ in assigned), ("silence", k_ in culprits)) if ok])
        for key, val in (("icao24", str(ts.icao24[k_])), ("callsign", str(ts.callsign[k_])),
                         ("class", aircraft_types.CLASS_NAMES[ts.cls[k_]]), ("type_code", tc.get(ts.icao24[k_], "")),
                         ("alt_source", str(ts.alt_source[k_])), ("role", role), ("n_candidates", n_assigned.get(k_, 0))):
            trow[key].append(val)
    buf.add("tracks", gpd.GeoDataFrame(trow, geometry=lines, crs="EPSG:4326"))

    # fixed layer set: create empty layers that got no rows
    empty_poly = gpd.GeoDataFrame({"cell_id": pd.Series([], dtype="int64")}, geometry=gpd.GeoSeries([], crs="EPSG:4326"))
    for name in ("stage_cpa", "stage_offset", "stage_silence") + (("grid",) if write_grid else ()):
        buf.ensure(name, empty_poly)
    for name, cols in (("candidates", cand), ("event_cpa_points", prow), ("tracks", trow)):
        buf.ensure(name, gpd.GeoDataFrame({k_: pd.Series([], dtype="object") for k_ in cols},
                                          geometry=gpd.GeoSeries([], crs="EPSG:4326")))
    layer_rows = buf.close()

    ras = export.write_score_raster(
        run_dir / "score.tif", np.array([d["i"] for d in surv], np.int64), np.array([d["j"] for d in surv], np.int64),
        np.array([d["score"] for d in surv], np.float32), spec.spacing_m, spec.crs,
        "Locked N-event score: pooled mean Huber loss of ridge residuals in ridge-scatter units (lower is better)",
        int(oc.get("max_raster_cells", 50_000_000)))
    timings["export_s"] = _time.perf_counter() - t

    # Plots
    t = _time.perf_counter()
    vc = section(cfg, "viewer")
    plots = {}
    if not args.no_plots:
        plot.plot_funnel(counts, run_dir / "funnel.png", f"Cascade survivors — {run_id}")
        ncand = int(vc.get("max_candidates", 20))
        cands = []
        for d in surv[:ncand]:
            cands.append(plot.candidate_view(d, events, ts, prior, c, spec))
        fmax = min(3900.0, max(300.0, 1.2 * max(float(e.ridge_f_hz.max()) for e in events)))
        disp = audio.display_spectrogram(x, fs, fmax, int(vc.get("max_time_px", 1500)), int(vc.get("max_freq_px", 400)))
        plot.plot_candidates_spectrogram(disp, events, cands, run_dir / "spectrogram_top.png",
                                         int(oc.get("top_plot", 3)),
                                         [e["t_cpa_received_rel_s"] for e in truth["events"]] if truth else None)
        plot.plot_score_histogram({"candidates (silence survivors)": np.array([d["score"] for d in surv])},
                                  run_dir / "score_hist.png", "locked N-event score (lower is better)",
                                  true_score=next((d["score"] for d in surv
                                                   if truth and d["cell_id"] == int(spec.cell_ids(*true_ij))), None))
        plotly_js = root / "plotly.min.js"
        plot.write_viewer(run_dir / "viewer.html", run_id, x, fs, prior.start_s, events, fits, cands,
                          "../plotly.min.js", vc, sp, c)
        if not plotly_js.exists():
            print(f"warning: {plotly_js} missing; viewer.html needs it (pinned plotly.js {vc.get('plotly_version')})",
                  file=sys.stderr)
        plots = {"funnel": "funnel.png", "spectrogram": "spectrogram_top.png", "histogram": "score_hist.png",
                 "viewer": "viewer.html"}
    timings["plots_s"] = _time.perf_counter() - t

    # Truth evaluation
    truth_eval = None
    if truth:
        tid = int(spec.cell_ids(*true_ij))
        tc_ = next((d for d in surv if d["cell_id"] == tid), None)
        from pyproj import Geod

        g = Geod(ellps="WGS84")
        best = surv[0] if surv else None
        err = g.inv(truth["mic"]["lon"], truth["mic"]["lat"], best["est_lon"], best["est_lat"])[2] if best else None
        err_c = g.inv(truth["mic"]["lon"], truth["mic"]["lat"], best["lon"], best["lat"])[2] if best else None
        ib = min(surv, key=lambda d: d["interim_rank"]) if surv else None
        # match each truth event to the detected event nearest in received CPA time (realistic scenes can
        # contain extra audible aircraft, which become extra events)
        match = []
        for te in truth["events"]:
            k_ = int(np.argmin([abs(e.t_rx_s - te["t_cpa_received_rel_s"]) for e in events]))
            match.append(k_ if abs(events[k_].t_rx_s - te["t_cpa_received_rel_s"]) < 10 else None)
        true_assign = None
        if tc_ is not None:
            true_assign = [None if (k_ is None or tc_["tracks"][k_] < 0) else str(ts.icao24[tc_["tracks"][k_]])
                           for k_ in match]
        clock_err = (truth.get("realism") or {}).get("clock_error_s", 0.0) or 0.0
        truth_eval = {"true_cell_id": tid, "true_cell_ij": true_ij, "true_cell_reached": true_stage,
                      "survived_all_stages": true_stage == "silence",
                      "rank": tc_["rank"] if tc_ else None, "score": tc_["score"] if tc_ else None,
                      "rank1_error_m": err, "rank1_cell_centre_error_m": err_c,
                      "rank1_score": best["score"] if best else None,
                      "rank_by_centre_score": (sorted(surv, key=lambda d: d["score_centre"]).index(tc_) + 1) if tc_ else None,
                      "true_cell_assignment": true_assign,
                      "true_cell_assignment_correct": (true_assign is not None and
                                                       true_assign == [e["icao24"] for e in truth["events"]]),
                      "truth_events_detected": sum(k_ is not None for k_ in match),
                      "extra_events": len(events) - sum(k_ is not None for k_ in match),
                      "rank1_dt_error_s": (best["dt_s"] + clock_err) if best else None,
                      "interim_rank": tc_["interim_rank"] if tc_ else None,
                      "ambiguity": amb["status"], "true_cell_in_tied_set": tid in tied_ids,
                      "correct": bool(tc_ is not None and tc_["rank"] == 1 and amb["status"] == "unique"),
                      "interim_rank1_error_m": g.inv(truth["mic"]["lon"], truth["mic"]["lat"], ib["lon"], ib["lat"])[2]
                      if ib else None}

    timings["total_s"] = _time.perf_counter() - T0
    info = {
        "run_id": run_id, "created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "git": _git_state(),
        "human_in_loop": False, "manual_inputs": manual_inputs(cfg, args),
        "milestone": 5, "score": {"definition": "pooled mean Huber loss of ridge residuals in ridge-scatter units; "
                                                "one shared Δt, one f0 per event; lower is better; "
                                                "cell score = best point inside the cell when score.refine_in_cell",
                                  "interim_columns": "interim_rank/interim_cost = cascade consistency, for comparison"},
        "inputs": {"audio": audio_path, "tracks": tracks_path, "registry": registry, "truth": truth_path,
                   "region": region_name, "dem": section(cfg, "dem").get("path"), "geoid": geoid.source},
        "search": {"mode": mode, "grid_crs": spec.crs.to_string(), "spacing_m": spec.spacing_m,
                   "tile_cells": spec.tile_cells, "tiles_total": setup.n_tiles_total,
                   "tiles_active": len(setup.tile_origins), "tiles_run": n_tiles_run, "grid_layer": write_grid,
                   "grid_count_exact": grid_exact},
        "time_prior": {"mode": prior.mode, "start_utc": export._iso(prior.start_s), "dt_lo_s": prior.dt_lo_s,
                       "dt_hi_s": prior.dt_hi_s, "duration_s": duration},
        "speed_of_sound_mps": c,
        "events": [{"event": e.idx, "class": aircraft_types.CLASS_NAMES[e.cls], "t_rx_rel_s": e.t_rx_s,
                    "v_mps": e.v_mps, "d_m": e.d_m, "f0_hz": e.f0_hz, "sigma_v_d_t": e.sigma,
                    "coverage_tau": e.coverage_tau,
                    "tolerances": {"v_mps": tl.v_mps, "d_core_m": tl.d_core_m, "d_grid_m": tl.d_grid_m,
                                   "t_rx_s": tl.t_rx_s, "w_cov": tl.w_cov}}
                   for e, tl in zip(events, setup.tolerances)],
        "n_tracks": {"prepared_segments": len(prepared), "in_trackset": ts.n_tracks,
                     "assigned": len(assigned), "silence_culprits": len(culprits)},
        "survivors": counts,
        "ambiguity": {k_: v for k_, v in amb.items() if k_ != "tied"},
        "silence_removed": counts["offset"] - counts["silence"],
        "layers": layer_rows, "score_raster": ras, "plots": plots,
        "timings_s": timings, "truth_eval": truth_eval,
        "config": cfg,
    }
    export.write_run_json(run_dir, info)
    export.update_latest(root, run_dir)
    fp = json.loads((run_dir / "run.json").read_text())["disk_footprint"]

    print(f"run {run_id}: region {region_name}, {len(events)} events, {ts.n_tracks} track segments, grid {spec.crs.to_string()}")
    for e, tl in zip(events, setup.tolerances):
        print(f"  event {e.idx} ({aircraft_types.CLASS_NAMES[e.cls]}): t_rx {e.t_rx_s:.2f}s v {e.v_mps:.1f}±{tl.v_mps:.1f}"
              f" d {e.d_m:.0f}±{tl.d_core_m + tl.d_grid_m:.0f} t±{tl.t_rx_s:.2f}s (w_cov {tl.w_cov:g})")
    print("  survivors: " + " -> ".join(f"{k} {counts[k]:,}" for k in cascade.STAGES)
          + f"  (silence removed {info['silence_removed']})")
    if truth_eval:
        print(f"  true cell: reached {true_stage}; rank {truth_eval['rank']} of {len(surv)} "
              f"(interim {truth_eval['interim_rank']}); rank-1 error "
              f"{truth_eval['rank1_error_m'] and round(truth_eval['rank1_error_m'])} m; "
              f"assignment correct: {truth_eval['true_cell_assignment_correct']}")
    print(f"  ranking: {amb['status']} ({amb['n_tied']} tied within {sp.ambiguity_rel:.0%}, spread "
          f"{amb['spread_m'] and round(amb['spread_m'])} m, rank-2/rank-1 {amb['gap_ratio'] and round(amb['gap_ratio'], 2)})")
    for d in surv[:3]:
        print(f"  #{d['rank']} {d['est_lat']:.5f},{d['est_lon']:.5f} score {d['score']:.3f} Δt {d['dt_s']:+.2f}s "
              f"tracks {[str(ts.icao24[k_]) if k_ >= 0 else '—' for k_ in d['tracks']]} "
              f"rms {np.round(d['rms'], 2).tolist()} Hz silence {d['sil_pen']:.1f} nats")
    if info["manual_inputs"]:
        print(f"  NOTE: manual inputs used {sorted(info['manual_inputs'])}: not a fully automated run")
    print(f"  time {timings['total_s']:.1f}s (events {timings['events_s']:.1f}, cascade "
          f"{sum(stage_s.values()):.2f}, score {timings['score_s']:.2f} for {n_scored} hypotheses); disk {(fp['total_bytes'] + fp['run_json_bytes']) / 1e6:.2f} MB -> {run_dir}")
    return 0


def cmd_clean(args) -> int:
    from doppler import export

    cfg = load_config(args.config)
    root = Path(section(cfg, "outputs").get("root", "outputs"))
    keep = args.keep if args.keep is not None else int(section(cfg, "outputs").get("keep_runs", 5))
    if not root.exists():
        print(f"{root} does not exist")
        return 0
    removed, freed = export.prune_runs(root, keep)
    for r in removed:
        print(f"  removed {r}")
    print(f"kept {len(export.list_runs(root))} run(s); freed {freed / 1e6:.2f} MB")
    return 0


def main(argv=None) -> int:
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Acoustic Doppler geolocation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth", help="write a synthetic scenario (audio, tracks, ground truth, diagnostic plot)")
    s.add_argument("--config", default="config.yaml")
    s.add_argument("--out", help="scenario directory (default outputs/scenarios/synth_seed<seed>)")
    s.add_argument("--region", help="search region name (default region.name)")
    s.add_argument("--seed", type=int)
    s.add_argument("--snr-db", dest="snr_db", type=float)
    s.add_argument("--n-tracks", dest="n_tracks", type=int)
    s.add_argument("--duration-s", dest="duration_s", type=float)
    s.add_argument("--event-times", dest="event_times", help="received CPA times, e.g. 40,960")
    s.add_argument("--event-classes", dest="event_classes", help="e.g. prop,jet")
    s.add_argument("--clean", action="store_true", help="no realism layer (ideal ADS-B, clean source and path)")
    s.add_argument("--cascade-decoys", dest="cascade_decoys", type=int,
                   help="decoys over CPA/offset survivors (default synth.n_cascade_decoys; 0 disables)")
    s.set_defaults(func=cmd_synth)

    e = sub.add_parser("events", help="detect and fit flyover events in a recording")
    e.add_argument("--config", default="config.yaml")
    e.add_argument("--audio", required=True)
    e.add_argument("--sidecar", help="time-prior JSON (default: <audio>.json)")
    e.add_argument("--out", help="write events (.json, or .parquet + _ridges.parquet)")
    e.set_defaults(func=cmd_events)

    t = sub.add_parser("tracks", help="ingest, clean and classify ADS-B tracks; print a summary")
    t.add_argument("--config", default="config.yaml")
    t.add_argument("--tracks", required=True)
    t.add_argument("--registry", help="aircraft registry CSV (default: aircraft_types.registry_path)")
    t.set_defaults(func=cmd_tracks)

    g = sub.add_parser("regions", help="list or add search regions (inputs/search_regions.gpkg)")
    g.add_argument("action", choices=["list", "add"])
    g.add_argument("--config", default="config.yaml")
    g.add_argument("--path")
    g.add_argument("--name")
    g.add_argument("--lat", type=float)
    g.add_argument("--lon", type=float)
    g.add_argument("--radius-m", dest="radius_m", type=float)
    g.add_argument("--replace", action="store_true")
    g.set_defaults(func=cmd_regions)

    l = sub.add_parser("locate", help="run the cascade (milestone 4; scoring is milestone 5) and write a run")
    l.add_argument("--config", default="config.yaml")
    l.add_argument("--scenario", help="synthetic scenario dir (audio.flac, audio.json, tracks.parquet, "
                                      "aircraft_db.csv, truth.json)")
    l.add_argument("--audio")
    l.add_argument("--sidecar")
    l.add_argument("--tracks")
    l.add_argument("--registry")
    l.add_argument("--truth", help="truth.json: report the true cell's stage, rank and error distance")
    l.add_argument("--region")
    l.add_argument("--run-id", dest="run_id")
    l.add_argument("--write-grid", dest="write_grid", action="store_true", default=None,
                   help="write the full grid layer (default: regional runs only)")
    l.add_argument("--no-plots", dest="no_plots", action="store_true")
    l.set_defaults(func=cmd_locate)

    k = sub.add_parser("clean", help="prune old runs in outputs/ (keep the newest N)")
    k.add_argument("--config", default="config.yaml")
    k.add_argument("--keep", type=int)
    k.set_defaults(func=cmd_clean)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
