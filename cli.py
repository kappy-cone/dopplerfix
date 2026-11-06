"""Command line: synth | events | tracks | adsblol | locate | verify-result.

    python cli.py synth  --config config.yaml --out outputs/synth_seed0 [--seed 0] [--event-times 150,420]
    python cli.py events --config config.yaml --audio outputs/synth_seed0/audio.wav [--out events.json]
    python cli.py tracks --config config.yaml --tracks outputs/synth_seed0/tracks.csv --registry .../aircraft_db.csv
    python cli.py locate ...   (milestones 4-5; --null-test / --commit-result: report-only diagnostics)
    python cli.py verify-result outputs/<run>  [--sha256 <hash kept elsewhere>]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import yaml

from doppler import aircraft_types, audio, classify, detect, fit, physics, ridge, synth, tracks
from doppler.geo import ArrayDEM, FlatDEM, GeoidModel, LocalFrame


def load_config(path: str | Path, variant: str | None = None) -> dict:
    """Read config.yaml; with ``variant``, deep-merge ``variants[variant]`` onto it (see apply_variant)."""
    with open(path) as fh:
        cfg = yaml.safe_load(fh) or {}
    return apply_variant(cfg, variant) if variant else cfg


def deep_merge(base: dict, over: dict) -> dict:
    """Recursive dict merge: values in ``over`` win; nested dicts merge key by key."""
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def apply_variant(cfg: dict, name: str) -> dict:
    """The named design variant (HANDOFF "Two design variants"): config.yaml with ``variants[name]`` merged on.

    The resolved config records ``variant: name``. Variant overlays are named design choices, not per-clip
    manual inputs.
    """
    variants = cfg.get("variants") or {}
    if name not in variants:
        raise SystemExit(f"unknown variant {name!r} (config.yaml variants: {sorted(variants)})")
    out = deep_merge(cfg, variants[name] or {})
    out["variant"] = name
    return out


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


# Config sections that run_events reads (event_params + the speed of sound). Two configs with equal values here
# give the same fitted events from the same audio.
DETECTION_SECTIONS = ("spectrogram", "spectrogram_jet", "detect", "ridge", "ridge_jet", "fit", "classify", "environment")


def detection_key(cfg: dict) -> str:
    """SHA-256 of the config sections detection depends on (DETECTION_SECTIONS)."""
    import hashlib

    blob = json.dumps({k: cfg.get(k) for k in DETECTION_SECTIONS}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def events_file_name(variant_cfg: dict, base_cfg: dict) -> str:
    """Kept events file of an ablation unit for a variant: events.parquet when the variant detects like the base
    config, else events_<key>.parquet (its own detection)."""
    k = detection_key(variant_cfg)
    return "events.parquet" if k == detection_key(base_cfg) else f"events_{k[:12]}.parquet"


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
def cascade_params(cfg: dict):
    """CascadeParams from config (cascade:, plus the two hypothesis keys that live under score:)."""
    from doppler import cascade

    cp_cfg = {**section(cfg, "cascade"), **{k: v for k, v in section(cfg, "score").items()
                                            if k in ("allow_shared_tracks", "max_hypotheses_per_cell")}}
    return cascade.CascadeParams.from_dict(cp_cfg)


def cascade_inputs(cfg: dict, fits, prepared: list[tracks.Track], tprior, duration_s: float, c: float,
                   region_lonlat):
    """ObservedEvents, Δt prior, windowed TrackSet, grid spec and cascade params."""
    from doppler import cascade, geo

    cp = cascade_params(cfg)
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


def scenario_detection_cfg(cfg: dict) -> dict:
    """The config the scenario builder detects with when it places cascade decoys: config.yaml with
    ``synth.scenario_detection`` merged on. It pins the benchmark scenarios to a fixed detector, so a change of
    the method's detection default does not also change the scenarios it is measured on."""
    return deep_merge(cfg, section(cfg, "synth").get("scenario_detection") or {})


def build_synth_scenario(cfg: dict, region, params: synth.SynthParams, rp, n_dec: int):
    """One synthetic scenario, with up to ``n_dec`` cascade decoys over CPA/offset survivors far from the mic
    (only the silence filter can remove those cells, so they test it; separate random stream, events unchanged).
    Returns (scenario, decoy targets, number of CPA/offset survivor cells)."""
    frame0, _ = region_frame(region)
    tp = audio.parse_time_prior(cfg.get("time_prior") or {})
    c = speed_of_sound_from(cfg)
    ground = section(cfg, "region").get("ground_elev_m", 0.0)
    frame = LocalFrame(frame0.lat0, frame0.lon0, ground)
    sc = synth.make_scenario(params, frame, tp.start_s, ground, c, realism=rp)
    targets, n_cands = [], 0
    if n_dec > 0:
        _, fits0 = run_events(sc.audio, sc.fs_hz, scenario_detection_cfg(cfg), c)   # decoy placement only
        tp_ = tracks.TrackParams.from_dict(section(cfg, "tracks"))
        src = sc.adsb_frame if sc.adsb_frame is not None else None
        prep = (tracks.prepare_tracks(tracks.tracks_from_frame(src, tp_), tp_) if src is not None
                else tracks.prepare_tracks(sc.tracks, tp_))
        classify_tracks(cfg, prep, sc.registry)
        cands = offset_survivors_enu(cfg, fits0, prep, tp, params.duration_s, c, region, frame, FlatDEM(ground),
                                     params.mic_height_m)
        n_cands = len(cands)
        targets = choose_decoy_targets(cands, sc.mic_enu, params, n_dec)
        if targets:
            sc = synth.make_scenario(params, frame, tp.start_s, ground, c, mic_enu=sc.mic_enu,
                                     decoy_targets_enu=targets, realism=rp)
    return sc, targets, n_cands


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
    n_dec = params.n_cascade_decoys if args.cascade_decoys is None else args.cascade_decoys
    sc, targets, n_cands = build_synth_scenario(cfg, region, params, rp, n_dec)
    if n_dec > 0:
        print(f"cascade decoys: {len(targets)} of {n_dec} placed over CPA/offset survivors "
              f"({n_cands} survivors, feasible need ≥ "
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
        out = Path(args.out)
        export.write_events(fits, out, tp.start_s)
        if out.suffix == ".parquet":     # provenance for `locate --events` (P0.3 seam)
            out.with_name(out.stem + ".meta.json").write_text(json.dumps({
                "source": "detected", "audio_sha256": file_sha256(args.audio),
                "event_config_sha256": event_config_sha256(cfg), "events_sha256": file_sha256(out),
                "created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "git": _git_state()}, indent=1))
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


def cmd_adsblol(args) -> int:
    """Stream an adsb.lol daily archive (its split tar parts) and keep only the region and time window.

    Writes canonical track Parquet (``--out``) and, from the archive's database fields, a registry CSV
    (``<out stem>_types.csv``: icao24, typecode, registration) for aircraft_types. The archive is read in one
    pass and never unpacked to disk (docs/adsblol_ingest.md). A single ``-`` reads the whole tar from stdin, so
    the parts need not be stored: ``(curl -sSfL <aa>; curl -sSfL <ab>) | python cli.py adsblol - ...``.
    """
    from doppler import adsblol, export

    cfg = load_config(args.config)
    p = adsblol.AdsbLolParams.from_dict(section(cfg, "adsblol"))
    _, region = load_region(cfg, args.region)
    lon_lo, lat_lo, lon_hi, lat_hi = region.bounds
    bbox = adsblol.padded_bbox(lat_lo, lat_hi, lon_lo, lon_hi, p.margin_m)
    t_lo = pd.Timestamp(args.start).tz_localize("UTC").timestamp() if pd.Timestamp(args.start).tz is None \
        else pd.Timestamp(args.start).timestamp()
    t_hi = pd.Timestamp(args.end).tz_localize("UTC").timestamp() if pd.Timestamp(args.end).tz is None \
        else pd.Timestamp(args.end).timestamp()
    if t_hi <= t_lo:
        print("--end must be after --start", file=sys.stderr)
        return 2
    src = sys.stdin.buffer if args.parts == ["-"] else args.parts
    frames, types = [], []
    for df, row in adsblol.extract(export.iter_archive_json(src), bbox, t_lo - p.pad_s, t_hi + p.pad_s, p):
        frames.append(df)
        if row:
            types.append(row)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tracks_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=adsblol.COLUMNS)
    export.write_parquet(tracks_df[adsblol.COLUMNS], out)
    pd.DataFrame(types, columns=["icao24", "typecode", "registration"]).drop_duplicates("icao24").to_csv(
        out.with_name(out.stem + "_types.csv"), index=False)
    n_air = tracks_df["icao24"].nunique()
    print(f"{len(tracks_df)} fixes from {n_air} aircraft ({len(types)} with a type code) in "
          f"{bbox[0]:.3f}..{bbox[1]:.3f} N, {bbox[2]:.3f}..{bbox[3]:.3f} E -> {out}")
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


def manual_inputs(cfg: dict, events_prov: dict | None = None) -> dict:
    """Per-clip manual inputs that would make a run not fully automated (CLAUDE.md Operating modes).

    Recorded automatically: manual detection windows, and an events file whose provenance does not match this
    run (edited, from other audio or other event settings; see load_events_file).
    """
    m = {}
    if section(cfg, "detect").get("manual_windows"):
        m["detect.manual_windows"] = section(cfg, "detect")["manual_windows"]
    if events_prov and events_prov.get("manual"):
        m["events_file"] = {"path": events_prov.get("path"), "reasons": events_prov.get("reasons")}
    return m


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, dict) and v:
            out.update(_flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def config_provenance(cfg: dict, config_path: str | Path) -> dict:
    """Which configuration a run used, against the committed config.yaml (P0.1h).

    ``config_overrides`` lists every key whose value differs from the committed file with the same variant
    applied (dotted key -> [committed, used]); keys absent on one side show null. Named variants are not
    overrides. An edited tolerance therefore shows up here even if nobody declares it.
    """
    import hashlib
    import subprocess

    path = Path(config_path)
    raw = path.read_bytes() if path.exists() else b""
    out = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "variant": cfg.get("variant"),
           "committed_sha256": None, "config_overrides": None}
    try:
        rel = path.resolve().relative_to(Path.cwd().resolve())
        committed = subprocess.run(["git", "show", f"HEAD:{rel.as_posix()}"], capture_output=True, check=True).stdout
    except Exception:
        try:
            committed = subprocess.run(["git", "show", "HEAD:config.yaml"], capture_output=True, check=True).stdout
        except Exception:
            return out
    out["committed_sha256"] = hashlib.sha256(committed).hexdigest()
    base = yaml.safe_load(committed) or {}
    if cfg.get("variant"):
        # the variant's overlay as committed, else as in the file used (a new variant not committed yet)
        overlays = base.get("variants") or (yaml.safe_load(raw) or {}).get("variants") or {}
        if cfg["variant"] in overlays:
            base = deep_merge(base, overlays[cfg["variant"]] or {})
    a, b = _flatten({k: v for k, v in base.items() if k != "variant"}), \
        _flatten({k: v for k, v in cfg.items() if k != "variant"})
    out["config_overrides"] = {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b))
                               if k != "variants" and not k.startswith("variants.") and a.get(k) != b.get(k)}
    return out


class _NullSink:
    """LayerBuffer stand-in for in-process runs (ablation): nothing is written."""

    def add(self, *_a, **_k) -> None:
        pass

    def ensure(self, *_a, **_k) -> None:
        pass

    def close(self) -> dict:
        return {}


@dataclass
class LocateInputs:
    """Explicit inputs of one locate run (P0.3 stage seam). Paths only; nothing is read until run_locate."""

    audio_path: str
    tracks_path: str
    registry: str | None = None
    truth_path: str | None = None
    region: str | None = None
    sidecar: str | None = None
    events_path: str | None = None     # events file (cli.py events --out X.parquet) instead of detection
    write_grid: bool | None = None
    tag: str = ""


def locate_inputs(args) -> LocateInputs | None:
    """LocateInputs from the CLI arguments (a --scenario directory fills in whatever is not given)."""
    sc_dir = Path(args.scenario) if getattr(args, "scenario", None) else None
    audio_path = args.audio or (sc_dir and next((str(sc_dir / n) for n in ("audio.flac", "audio.wav")
                                                  if (sc_dir / n).exists()), None))
    tracks_path = args.tracks or (sc_dir and str(sc_dir / "tracks.parquet"))
    registry = args.registry or (sc_dir and (sc_dir / "aircraft_db.csv").exists() and str(sc_dir / "aircraft_db.csv")) or None
    truth_path = args.truth or (sc_dir and (sc_dir / "truth.json").exists() and str(sc_dir / "truth.json")) or None
    if not audio_path or not tracks_path:
        return None
    return LocateInputs(audio_path, tracks_path, registry, truth_path, args.region, args.sidecar,
                        getattr(args, "events", None), args.write_grid, sc_dir.name if sc_dir else "")


@dataclass
class LocateResult:
    """Everything one locate run computed, in memory (written by write_outputs, or read by the ablation)."""

    cfg: dict
    inputs: LocateInputs
    timings: dict
    amb: object = None
    buf: object = None
    c: object = None
    counts: object = None
    culprits: object = None
    duration: object = None
    enum_stats: object = None
    events: object = None
    fits: object = None
    fs: object = None
    geoid: object = None
    grid_exact: object = None
    mode: object = None
    n_scored: object = None
    n_tiles_run: object = None
    prepared: object = None
    prior: object = None
    region_name: object = None
    run_dir: object = None
    run_id: object = None
    setup: object = None
    sidecar: object = None
    sp: object = None
    spec: object = None
    stage_s: object = None
    surv: object = None
    tied_ids: object = None
    status: object = None
    abs_fit: object = None
    true_ij: object = None
    true_stage: object = None
    truth: object = None
    truth_eval: object = None
    ts: object = None
    write_grid: object = None
    x: object = None
    events_prov: object = None


def run_locate(cfg: dict, inp: LocateInputs, *, fits=None, audio_data=None, sink_factory=None) -> LocateResult | None:
    """Events -> tracks -> cascade -> locked scoring -> truth evaluation, all in memory.

    ``fits``/``audio_data`` (x, fs): reuse what an earlier run computed (the ablation shares one detection
    across variants). ``sink_factory() -> (run_dir, LayerBuffer)`` receives the streamed stage layers;
    without it nothing is written. Returns None when no events are detected.
    """
    import time as _time

    from doppler import cascade, export, geo, score

    timings: dict[str, float] = {}
    geoid = setup_geoid(cfg)
    dem_source = load_dem_source(cfg, geoid)
    region_name, region = load_region(cfg, inp.region)
    c = speed_of_sound_from(cfg)
    mic_h = float(section(cfg, "mic").get("height_agl_m", 1.5))
    mode = section(cfg, "search").get("mode", "regional")

    # Audio -> events
    t = _time.perf_counter()
    if audio_data is None:
        x, fs = sf.read(inp.audio_path, always_2d=False)
        x = audio.mix_to_mono(x)
    else:
        x, fs = audio_data
    duration = len(x) / fs
    sidecar = Path(inp.sidecar) if inp.sidecar else Path(inp.audio_path).with_suffix(".json")
    tprior = audio.parse_time_prior(json.loads(sidecar.read_text()) if sidecar.exists() else section(cfg, "time_prior"))
    if fits is not None:
        events_prov = {"source": "in-process", "manual": False}
    elif inp.events_path:
        fits, events_prov = load_events_file(inp.events_path, cfg, inp.audio_path)
    else:
        _, fits = run_events(x, fs, cfg, c)
        events_prov = {"source": "detected", "manual": False}
    timings["events_s"] = _time.perf_counter() - t
    if not fits:
        return None

    # Tracks (cropped to the window the cascade can use)
    t = _time.perf_counter()
    clock = float(section(cfg, "time_prior").get("clock_tol_s", 10.0))
    if tprior.mode == "known":
        win = (tprior.start_s - clock - 120.0, tprior.start_s + clock + duration + 60.0)
    elif tprior.mode == "window":
        win = (tprior.window_s[0] - clock - 120.0, tprior.window_s[1] + clock + duration + 60.0)
    else:
        win = None
    prepared = load_tracks(cfg, inp.tracks_path, inp.registry, win)
    events, prior, ts, spec, cp = cascade_inputs(cfg, fits, prepared, tprior, duration, c, region)
    timings["tracks_s"] = _time.perf_counter() - t

    write_grid = inp.write_grid if inp.write_grid is not None else \
        {"auto": mode == "regional", True: True, False: False}.get(section(cfg, "outputs").get("write_grid", "auto"),
                                                                     mode == "regional")
    run_dir, buf = sink_factory() if sink_factory is not None else (None, _NullSink())
    run_id = run_dir.name if run_dir is not None else None

    # Truth (synthetic): the cell containing the true mic
    truth = json.loads(Path(inp.truth_path).read_text()) if inp.truth_path else None
    true_ij = None
    if truth:
        from pyproj import Transformer

        tx, ty = Transformer.from_crs("EPSG:4326", spec.crs, always_xy=True).transform(truth["mic"]["lon"], truth["mic"]["lat"])
        true_ij = (int(np.floor(tx / spec.spacing_m)), int(np.floor(ty / spec.spacing_m)))
    true_stage = "outside region/grid"
    # True aircraft assignment (synthetic): which TrackSet rows (segments) belong to each matched event's aircraft,
    # and the last cascade stage at which some cell still holds that assignment (P0.4 failure taxonomy).
    from doppler import evaluate as ev_

    ep = ev_.EvalParams.from_dict(section(cfg, "evaluate"))
    match, true_rows, ta_rank = [], {}, 0
    if truth:
        match = ev_.match_events([e.t_rx_s for e in events], [te["t_cpa_received_rel_s"] for te in truth["events"]],
                                 ep.match_tol_s)
        for k_, te in zip(match, truth["events"]):
            if k_ is not None:
                true_rows[k_] = set(np.flatnonzero(ts.icao24 == te["icao24"]).tolist())
    ta_stage = ("grid" if true_rows and all(true_rows.values()) else "none") if truth else None

    def _true_pass_cells(passes, M_):
        have = np.ones(M_, bool)
        for k_, rows_ in true_rows.items():
            m_ = (passes.event == k_) & np.isin(passes.track, list(rows_)) if len(passes) else np.zeros(0, bool)
            h_ = np.zeros(M_, bool)
            h_[passes.cell[m_]] = True
            have &= h_
        return have

    def _true_hyp(tracks_row) -> bool:
        return all(int(tracks_row[k_]) in rows_ for k_, rows_ in true_rows.items())

    # Cascade, tile by tile
    t = _time.perf_counter()
    setup = cascade.prepare_cascade(spec, events, ts, prior, c, cp)
    timings["cascade_setup_s"] = _time.perf_counter() - t
    to_geo = geo.tiles_to_geodetic(spec)
    counts = {k: 0 for k in cascade.STAGES}
    stage_s = {k: 0.0 for k in cascade.STAGES}
    surv = []           # per silence survivor: dict
    offset_costs = []
    culprits = set()
    enum_stats: dict[str, int] = {}
    n_tiles_run = 0
    for r in cascade.iter_cascade(spec, events, ts, prior, dem_source, mic_h, c, cp, setup=setup,
                                  all_tiles=write_grid):
        n_tiles_run += 1
        tile = r.tile
        M = len(tile)
        for k_, v_ in r.seconds.items():
            stage_s[k_] += v_
        for k_, v_ in r.enum_stats.items():
            enum_stats[k_] = enum_stats.get(k_, 0) + int(v_)
        counts["grid"] += M
        counts["cpa"] += int(r.cpa.sum())
        counts["offset"] += int(r.offset.sum())
        counts["silence"] += int(r.silence.sum())
        if true_ij is not None and M:
            hit = np.flatnonzero((tile.i == true_ij[0]) & (tile.j == true_ij[1]))
            if len(hit):
                h = hit[0]
                true_stage = ("silence" if r.silence[h] else "offset" if r.offset[h] else "cpa" if r.cpa[h] else "grid")
        if true_rows and M and ta_stage in ("grid", "cpa", "offset"):
            if ta_stage == "grid" and (r.cpa & _true_pass_cells(r.passes, M)).any():
                ta_stage = "cpa"
            if len(r.hypotheses):
                th = np.array([_true_hyp(row_) for row_ in r.hypotheses.tracks], bool)
                if th.any():
                    ta_stage = "offset" if ta_stage != "silence" else ta_stage
                    if r.silence[r.hypotheses.cell[th]].any():
                        ta_stage = "silence"
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
    ridges_ = score.prepare_ridges(events, sp)
    if sp.ambiguity_mode == "v2":
        # P1.4 (comparison arm): Δnll tie set + assignment gap, locus, and the χ² absolute fit with P1.1's σ
        amb = score.ambiguity_v2([d["score"] for d in surv], [d["xyz_est"] for d in surv],
                                 [sum(k_ < 0 for k_ in d["tracks"]) for d in surv],
                                 [tuple(str(ts.icao24[k_]) if k_ >= 0 else "" for k_ in d["tracks"]) for d in surv],
                                 sum(k_ >= 0 for k_ in surv[0]["tracks"]) if surv else 0, sp)
        abs_fit = (score.gof_status(ridges_, ts, surv[0]["xyz_est"], surv[0]["tracks"], surv[0]["dt_s"],
                                    prior.start_s, c, sp) if surv else None)
        status = ("none" if not surv else "locus" if amb["status"] == "locus"
                  else "inconsistent" if abs_fit["inconsistent"] else amb["status"])
    else:
        amb = score.ambiguity([d["score"] for d in surv], [d["xyz_est"] for d in surv], sp,
                              [sum(k_ < 0 for k_ in d["tracks"]) for d in surv])
        # Run status (P0.4, provisional): the ambiguity status, overridden by "inconsistent" when the rank-1
        # hypothesis fits some matched event far worse than that event's model-free ridge scatter.
        sig_ref = [score.ridge_scatter_ref(r_.t_s, r_.f_hz, sp.ridge_decimate_s) for r_ in ridges_]
        abs_fit = score.absolute_fit_status(surv[0]["rms"], sig_ref, sp) if surv else None
        status = "none" if not surv else ("inconsistent" if abs_fit["inconsistent"] else amb["status"])
    tied_ids = {surv[k_]["cell_id"] for k_ in amb["tied"]}
    timings["score_s"] = _time.perf_counter() - t
    timings["n_hypotheses_scored"] = n_scored
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
        # P0.4 metrics (doppler/evaluate.py): rank-1 assignment, true-assignment survival / rank, outcome tier
        truth_icao = [te["icao24"] for te in truth["events"]]
        r1_icao = [str(ts.icao24[k_]) if k_ >= 0 else None for k_ in best["tracks"]] if best else []
        r1_ok = ev_.assignment_correct(r1_icao, match, truth_icao) if best else None
        ta_final = "scored" if ta_stage == "silence" else ta_stage
        ta_best = next((d for d in surv if true_rows and _true_hyp(d["tracks"])), None)
        ta_cell_rank = ta_best["rank"] if ta_best else None
        groups, ta_group_rank = [], None
        for d in surv:
            key = tuple(str(ts.icao24[k_]) if k_ >= 0 else "" for k_ in d["tracks"])
            if key not in groups:
                groups.append(key)
                if ta_group_rank is None and true_rows and _true_hyp(d["tracks"]):
                    ta_group_rank = len(groups)
        az_, dist_ = None, None
        if best:
            az_, _, dist_ = g.inv(truth["mic"]["lon"], truth["mic"]["lat"], best["est_lon"], best["est_lat"])
        de_, dn_ = (dist_ * np.sin(np.radians(az_)), dist_ * np.cos(np.radians(az_))) if best else (None, None)
        per_event = []
        for j_, (k_, te) in enumerate(zip(match, truth["events"])):
            row_ = {"truth_event": j_, "icao24": te["icao24"], "detected_event": k_}
            if k_ is not None:
                e_, tl_ = events[k_], setup.tolerances[k_]
                row_.update(ev_.fit_z(e_.v_mps, e_.d_m, e_.t_rx_s, e_.sigma,
                                      (tl_.v_mps, tl_.d_core_m + tl_.d_grid_m, tl_.t_rx_s),
                                      te["v_mps"], te["d_m"], te["t_cpa_received_rel_s"]))
                f_ = fits[k_]
                row_["harmonic_hops"] = ev_.harmonic_hops(f_.ridge.f_hz, f_.model(f_.ridge.t_s), ep.hop_ratio_tol)
                row_["gates_pruning"] = bool(setup.gating[k_])
            if best:
                row_["along_m"], row_["cross_m"] = ev_.along_cross(de_, dn_, te["heading_deg"])
            per_event.append(row_)
        truth_eval.update({
            "status": status, "rank1_assignment": r1_icao, "rank1_assignment_correct": r1_ok,
            "true_assignment_stage": ta_final, "true_assignment_cell_rank": ta_cell_rank,
            "true_assignment_group_rank": ta_group_rank, "n_assignment_groups": len(groups),
            # Δcost needs like-for-like scores: ranking is lexicographic in the unmatched count (plan §8.1)
            "true_assignment_score": ta_best["score"] if ta_best else None,
            "true_assignment_n_unmatched": sum(k_ < 0 for k_ in ta_best["tracks"]) if ta_best else None,
            "true_cell_n_unmatched": sum(k_ < 0 for k_ in tc_["tracks"]) if tc_ else None,
            "n_truth_events": len(truth["events"]),
            "rank1_error_en_m": [de_, dn_] if best else None, "per_event": per_event,
            "outcome": ev_.outcome(status, err, r1_ok, ep),
            "failure": ev_.failure_class(ta_final, r1_ok, err, ep),
            "legacy_correct": truth_eval["correct"],
        })

    return LocateResult(cfg=cfg, inputs=inp, timings=timings,
                        amb=amb,
                        buf=buf,
                        c=c,
                        counts=counts,
                        culprits=culprits,
                        duration=duration,
                        enum_stats=enum_stats,
                        events=events,
                        fits=fits,
                        fs=fs,
                        geoid=geoid,
                        grid_exact=grid_exact,
                        mode=mode,
                        n_scored=n_scored,
                        n_tiles_run=n_tiles_run,
                        prepared=prepared,
                        prior=prior,
                        region_name=region_name,
                        run_dir=run_dir,
                        run_id=run_id,
                        setup=setup,
                        sidecar=sidecar,
                        sp=sp,
                        spec=spec,
                        stage_s=stage_s,
                        surv=surv,
                        tied_ids=tied_ids,
                        status=status,
                        abs_fit=abs_fit,
                        true_ij=true_ij,
                        true_stage=true_stage,
                        truth=truth,
                        truth_eval=truth_eval,
                        ts=ts,
                        write_grid=write_grid,
                        x=x,
                        events_prov=events_prov,
                        )


def load_events_file(path: str | Path, cfg: dict, audio_path: str | Path) -> tuple[list, dict]:
    """Events from ``cli.py events --out X.parquet`` plus a provenance check (the events seam, P0.3).

    The file counts as automated only if its X.meta.json says ``source: detected`` and its audio and event-config
    hashes match this run. Anything else (no meta, edited, other audio or settings) is recorded as a manual input.
    """
    from doppler import export

    path = Path(path)
    meta_p = path.with_name(path.stem + ".meta.json")
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    fits = export.read_events(path)
    reasons = []
    if meta.get("source") != "detected":
        reasons.append("no detection provenance")
    if meta.get("audio_sha256") != file_sha256(audio_path):
        reasons.append("audio differs")
    if meta.get("event_config_sha256") != event_config_sha256(cfg):
        reasons.append("event settings differ")
    if meta.get("events_sha256") != file_sha256(path):
        reasons.append("events file edited")
    return fits, {"source": "file", "path": str(path), "manual": bool(reasons), "reasons": reasons}


EVENT_SECTIONS = ("spectrogram", "spectrogram_jet", "detect", "ridge", "ridge_jet", "fit", "classify", "environment")


def event_config_sha256(cfg: dict) -> str:
    import hashlib

    blob = json.dumps({k: cfg.get(k) for k in EVENT_SECTIONS}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def file_sha256(path: str | Path) -> str | None:
    import hashlib

    p = Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_outputs(res: LocateResult, args, T0: float, extra: dict | None = None) -> int:
    """Candidates, tracks and CPA layers, score.tif, plots, viewer, run.json and the console summary.

    ``extra``: report-only blocks appended to run.json (the diagnostics: section); none when they are off.
    """
    import time as _time

    import geopandas as gpd
    from shapely.geometry import LineString, Point

    from doppler import cascade, export, geo, plot, score

    cfg, inp, timings = res.cfg, res.inputs, res.timings
    (amb, buf, c, counts, culprits, duration, enum_stats, events, fits, fs, geoid, grid_exact, mode, n_scored, n_tiles_run, prepared, prior, region_name, run_dir, run_id, setup, sidecar, sp, spec, stage_s, surv, tied_ids, true_ij, true_stage, truth, truth_eval, ts, write_grid, x, events_prov) = (res.amb, res.buf, res.c, res.counts, res.culprits, res.duration, res.enum_stats, res.events, res.fits, res.fs, res.geoid, res.grid_exact, res.mode, res.n_scored, res.n_tiles_run, res.prepared, res.prior, res.region_name, res.run_dir, res.run_id, res.setup, res.sidecar, res.sp, res.spec, res.stage_s, res.surv, res.tied_ids, res.true_ij, res.true_stage, res.truth, res.truth_eval, res.ts, res.write_grid, res.x, res.events_prov)
    oc = section(cfg, "outputs")
    root = Path(oc.get("root", "outputs"))
    audio_path, tracks_path, registry, truth_path = inp.audio_path, inp.tracks_path, inp.registry, inp.truth_path
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
                          "../plotly.min.js", vc, sp, c,
                          plot.map_layers(surv, ts, ncand, int(vc.get("map_max_cells", 20000)), 2.0,
                                          str(vc.get("map_style", "open-street-map"))))
        if not plotly_js.exists():
            print(f"warning: {plotly_js} missing; viewer.html needs it (pinned plotly.js {vc.get('plotly_version')})",
                  file=sys.stderr)
        plots = {"funnel": "funnel.png", "spectrogram": "spectrogram_top.png", "histogram": "score_hist.png",
                 "viewer": "viewer.html"}
    timings["plots_s"] = _time.perf_counter() - t

    timings["total_s"] = _time.perf_counter() - T0
    info = {
        "run_id": run_id, "created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "git": _git_state(),
        "human_in_loop": False, "manual_inputs": manual_inputs(cfg, events_prov),
        "variant": cfg.get("variant"), "config_provenance": config_provenance(cfg, args.config),
        "events_provenance": events_prov,
        "milestone": 5, "score": {"definition": ("calibrated negative log-likelihood in nats (P1.1 arm); "
                                                 if sp.likelihood == "calibrated" else
                                                 "pooled mean Huber loss of ridge residuals in ridge-scatter units; ")
                                                + "one shared Δt, one f0 per event; lower is better; "
                                                "cell score = best point inside the cell when score.refine_in_cell",
                                  "interim_columns": "interim_rank/interim_cost = cascade consistency, for comparison"},
        "inputs": {"audio": audio_path, "tracks": tracks_path, "registry": registry, "truth": truth_path,
                   "time_prior_source": str(sidecar) if sidecar.exists() else "config.yaml time_prior",
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
                    "coverage_tau": e.coverage_tau, "gates_pruning": bool(g_),
                    "tolerances": {"v_mps": tl.v_mps, "d_core_m": tl.d_core_m, "d_grid_m": tl.d_grid_m,
                                   "t_rx_s": tl.t_rx_s, "w_cov": tl.w_cov}}
                   for e, tl, g_ in zip(events, setup.tolerances, setup.gating)],
        "hypothesis_enumeration": enum_stats,
        "n_tracks": {"prepared_segments": len(prepared), "in_trackset": ts.n_tracks,
                     "assigned": len(assigned), "silence_culprits": len(culprits)},
        "survivors": counts,
        "ambiguity": {k_: v for k_, v in amb.items() if k_ != "tied"},
        "status": res.status, "absolute_fit": res.abs_fit,
        "silence_removed": counts["offset"] - counts["silence"],
        "layers": layer_rows, "score_raster": ras, "plots": plots,
        "timings_s": timings, "truth_eval": truth_eval,
        "config": cfg,
    }
    if extra:
        info.update(extra)
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


def cmd_locate(args) -> int:
    """Locate one recording: run_locate (in memory) then write_outputs (the outputs contract)."""
    import time as _time

    from doppler import export

    T0 = _time.perf_counter()
    cfg = load_config(args.config, getattr(args, "variant", None))
    inp = locate_inputs(args)
    if inp is None:
        print("locate needs --scenario, or --audio and --tracks", file=sys.stderr)
        return 2
    root = Path(section(cfg, "outputs").get("root", "outputs"))

    def sink():
        run_dir = export.new_run_dir(root, args.run_id, tag=inp.tag)
        return run_dir, export.LayerBuffer(run_dir / "results.gpkg")

    if getattr(args, "null_test", False) or getattr(args, "commit_result", False):
        cfg["diagnostics"] = {**section(cfg, "diagnostics"),
                              **({"null_test": True} if getattr(args, "null_test", False) else {}),
                              **({"commit_result": True} if getattr(args, "commit_result", False) else {})}
    res = run_locate(cfg, inp, sink_factory=sink)
    if res is None:
        print("no events detected", file=sys.stderr)
        return 1
    return write_outputs(res, args, T0, locate_diagnostics(res))


# ---------------------------------------------------------------------------
# Report-only diagnostics (config.yaml diagnostics:): blind result commitment, time-shift null tests
# ---------------------------------------------------------------------------
def commitment_records(res: LocateResult) -> list[dict]:
    """Canonical commitment records of the ranked candidates (search outputs only, no truth)."""
    from doppler import commitment

    return [commitment.candidate_record(d["rank"], d["est_lat"], d["est_lon"], d["score"], d["dt_s"],
                                        [str(res.ts.icao24[k_]) if k_ >= 0 else "" for k_ in d["tracks"]])
            for d in res.surv]


def real_search_best(res: LocateResult):
    """The real search's rank 1 in null-test terms."""
    from doppler import nulltest

    b = nulltest.SearchBest(n_events=len(res.events), n_candidates=len(res.surv),
                            survivors={k: int(res.counts[k]) for k in ("cpa", "offset", "silence")})
    if res.surv:
        d = res.surv[0]
        b.n_explained = int(sum(k_ >= 0 for k_ in d["tracks"]))
        b.score, b.dt_s, b.lat, b.lon = float(d["score"]), float(d["dt_s"]), float(d["est_lat"]), float(d["est_lon"])
        b.tracks = [str(res.ts.icao24[k_]) if k_ >= 0 else "" for k_ in d["tracks"]]
    return b


def run_null_tests(res: LocateResult, fracs) -> dict:
    """Shift the events against the tracks by each fraction of the clip (wrapped), search again, compare.

    Same grid, Δt prior, tracks and parameters as the real search. When shifted ridges reach past the real
    TrackSet window, the TrackSet is rebuilt over the wider span from the same prepared tracks (same time grid).
    """
    import time as _time

    from doppler import nulltest

    cfg = res.cfg
    dem_source = load_dem_source(cfg, res.geoid)
    mic_h = float(section(cfg, "mic").get("height_agl_m", 1.5))
    cp = cascade_params(cfg)
    D = float(res.duration)
    shifted, extra = [], []
    for f, s in zip(fracs, nulltest.shift_seconds(fracs, D)):
        t = _time.perf_counter()
        ev = nulltest.shift_events(res.events, s, D)
        lo, hi = nulltest.ridge_span_s(ev)
        ts = res.ts
        if lo < 0.0 or hi > D:
            ts = tracks.build_trackset(res.prepared, res.ts.t0_s + min(0.0, lo), float(res.ts.t_s[-1]) + max(0.0, hi - D),
                                       res.ts.dt_s)
        shifted.append((f, s, nulltest.search(res.spec, ev, ts, res.prior, dem_source, mic_h, res.c, cp, res.sp)))
        extra.append({"ridge_span_rel_s": [lo, hi], "seconds": _time.perf_counter() - t})
    out = nulltest.summarize(real_search_best(res), shifted)
    for row, x in zip(out["shifted"], extra):
        row.update(x)
    out["definition"] = ("events shifted by shift_frac of the clip against the ADS-B (each wrapped as a unit), "
                         "cascade + locked scoring rerun; report only (no threshold, ranking unchanged). "
                         "explained_gap > 0: the real timing explains more events than any shift; "
                         "best_shifted_over_real: score ratio when both explain the same number")
    return out


def locate_diagnostics(res: LocateResult) -> dict:
    """run.json blocks for the enabled diagnostics (empty when they are off, so outputs match a plain run).

    The commitment is computed first, from search outputs only, and printed before write_outputs reports
    anything truth-derived.
    """
    from doppler import commitment, nulltest

    dp = nulltest.DiagnosticsParams.from_dict(section(res.cfg, "diagnostics"))
    out = {}
    if dp.commit_result:
        out["result_commitment"] = commitment.commit(commitment_records(res), len(res.surv), dp.commit_top_k)
        print(f"result commitment (top {dp.commit_top_k}): sha256 {out['result_commitment']['sha256']}")
    if dp.null_test:
        out["null_test"] = nt = run_null_tests(res, dp.null_shifts_frac)
        r_, b_ = nt["real"], nt["shifted"][nt["best_shifted"]] if nt["best_shifted"] is not None else None
        print(f"null test: real rank 1 explains {r_['n_explained']}/{r_['n_events']} events, score "
              f"{r_['best_score'] if r_['best_score'] is None else round(r_['best_score'], 3)}"
              + (f"; best shift {b_['shift_frac']:g} of the clip ({b_['shift_s']:+.0f} s) explains "
                 f"{b_['n_explained']}, score {b_['best_score'] if b_['best_score'] is None else round(b_['best_score'], 3)}"
                 f" ({b_['n_candidates']} candidates); explained gap {nt['explained_gap']}, ratio "
                 f"{nt['best_shifted_over_real'] and round(nt['best_shifted_over_real'], 2)}" if b_ else ""))
    return out


def cmd_verify_result(args) -> int:
    """Check a run's saved candidates (results.gpkg) against its recorded commitment and an optional hash."""
    from doppler import export

    try:
        r = export.verify_result(Path(args.run), args.sha256)
    except (FileNotFoundError, KeyError, ValueError) as e:
        print(f"cannot verify: {e}", file=sys.stderr)
        return 2
    print(("OK" if r["ok"] else "MISMATCH") + f": recomputed {r['recomputed_sha256']}, recorded "
          f"{r['recorded_sha256']}" + (f", expected {r['expected_sha256']}" if r["expected_sha256"] else ""))
    if r.get("mismatched_ranks"):
        print(f"  ranks that differ from the recorded payload: {r['mismatched_ranks']}")
    return 0 if r["ok"] else 1


# ---------------------------------------------------------------------------
# Ablation driver (reports/combined_plan.md §3.0, P0.4/P0.6): paired, in-process, disk-safe
# ---------------------------------------------------------------------------
ABLATION_SCENARIOS = {"A": (600.0, (150.0, 420.0)), "C": (60.0, (15.0, 45.0)), "G": (300.0, (50.0, 150.0, 250.0))}
LOO_SUBGROUPS = ("rpm", "cwind", "mic_height")


def ablation_plan(name: str) -> list[tuple[str, str, int]]:
    """(scenario, realism config, seed) rows of a named plan. Configs: ``all`` (every realism toggle on),
    ``clean``, ``loo:<group>`` / ``sub:<subgroup>`` (all on except that), ``set:key=value[;key=value]``.
    Seeds are shared across configs, so every comparison is paired."""
    from doppler import realism as rl

    loo = [f"loo:{g}" for g in rl.ABLATION_GROUPS] + [f"sub:{g}" for g in LOO_SUBGROUPS]
    if name == "smoke":
        return [("C", "clean", 0)]
    if name == "pilot":
        return [("C", "all", k) for k in range(5)] + [("A", "all", 0)]
    if name == "stageA":
        rows = [("C", "all", k) for k in range(50)]
        rows += [("C", c_, k) for c_ in loo for k in range(20)]
        rows += [("C", "clean", k) for k in range(5)]
        # the skeptics' cheapest experiments (P0.6), 10 paired seeds each
        rows += [("C", c_, k) for c_ in ("set:corridor_frac=1", "sub:corridor", "sub:twins", "sub:reflection")
                 for k in range(10)]
        return rows
    if name == "stageA_ext":
        # U18 acceptance (plan §8.5): all-on C seeds 50-99, so that with Stage A's 0-49 every arm has n = 100
        return [("C", "all", k) for k in range(50, 100)]
    if name == "stageB":
        rows = []
        for sc in ("A", "G"):
            rows += [(sc, "all", k) for k in range(20)] + [(sc, c_, k) for c_ in loo for k in range(10)]
            rows += [(sc, "clean", k) for k in range(3)]
        return rows
    raise SystemExit(f"unknown ablation plan {name!r} (smoke, pilot, stageA, stageA_ext, stageB)")


def realism_for(cfg: dict, spec: str):
    """RealismParams for an ablation config spec (see ablation_plan)."""
    from dataclasses import replace

    from doppler import realism as rl

    on = rl.RealismParams.from_dict(section(cfg, "realism"))
    if spec == "all":
        return on
    if spec == "clean":
        return rl.RealismParams()
    if spec.startswith(("loo:", "sub:")):
        return rl.without(on, spec[4:])
    if spec.startswith("set:"):
        kw = {}
        for part in spec[4:].split(";"):
            k, v = part.split("=", 1)
            kw[k.strip()] = yaml.safe_load(v)
        return rl.RealismParams.from_dict({**{f: getattr(on, f) for f in on.__dataclass_fields__}, **kw}) \
            if kw else on
    raise ValueError(f"unknown realism config spec {spec!r}")


def ablate_row(unit: dict, variant: str, res, seconds: float) -> dict:
    """One summary row (all JSON-serialisable) from a LocateResult."""
    te = res.truth_eval or {}
    surv = res.surv or []
    return {
        "scenario": unit["scenario"], "config": unit["config"], "seed": unit["seed"], "variant": variant,
        "git": unit["git"], "config_sha256": config_provenance(res.cfg, unit["config_path"])["sha256"],
        "detection_sha256": detection_key(res.cfg),
        "outcome": te.get("outcome"), "status": res.status, "failure": te.get("failure"),
        "rank1_error_m": te.get("rank1_error_m"), "rank1_error_en_m": te.get("rank1_error_en_m"),
        "rank1_assignment_correct": te.get("rank1_assignment_correct"),
        "true_assignment_stage": te.get("true_assignment_stage"),
        "true_assignment_group_rank": te.get("true_assignment_group_rank"),
        "true_assignment_cell_rank": te.get("true_assignment_cell_rank"),
        "n_assignment_groups": te.get("n_assignment_groups"),
        "true_cell_rank": te.get("rank"), "true_cell_reached": te.get("true_cell_reached"),
        "legacy_correct": te.get("legacy_correct"), "ambiguity": (res.amb or {}).get("status"),
        "rank1_dt_error_s": te.get("rank1_dt_error_s"), "rank1_score": te.get("rank1_score"),
        "true_cell_score": te.get("score"),
        "n_candidates": len(surv), "n_events": len(res.events or []),
        "truth_events_detected": te.get("truth_events_detected"), "extra_events": te.get("extra_events"),
        "n_truth_events": te.get("n_truth_events"), "true_assignment_score": te.get("true_assignment_score"),
        "true_assignment_n_unmatched": te.get("true_assignment_n_unmatched"),
        "true_cell_n_unmatched": te.get("true_cell_n_unmatched"),
        "rank1_n_unmatched": sum(k_ < 0 for k_ in surv[0]["tracks"]) if surv else None,
        "survivors": res.counts, "hypothesis_enumeration": res.enum_stats,
        "n_hypotheses_scored": res.n_scored, "per_event": te.get("per_event"),
        "absolute_fit": res.abs_fit, "seconds": seconds,
        "timings_s": {k: v for k, v in res.timings.items() if isinstance(v, (int, float))},
    }


def _asdesigned_row(unit: dict, variant: str, sc_dir: Path, tmp: Path) -> dict:
    """Run the pinned as-designed code (a git worktree at tag variants-asdesigned) on the same scenario."""
    import subprocess
    import time as _time

    from doppler import evaluate as ev_

    code = Path(unit["asdesigned_dir"])
    cfg = load_config(code / "config.yaml")
    if variant == "V0-asdesigned":
        cfg = deep_merge(cfg, {"cascade": {"max_unmatched_events": 0, "silence_audibility": "hard"}})
    cfg.setdefault("outputs", {})["root"] = str(tmp / "out")
    cfgp = tmp / f"{variant}.yaml"
    cfgp.write_text(yaml.safe_dump(cfg))
    t0 = _time.perf_counter()
    row = {"scenario": unit["scenario"], "config": unit["config"], "seed": unit["seed"], "variant": variant,
           "git": {"tag": "variants-asdesigned"}}
    try:
        r = subprocess.run([sys.executable, "cli.py", "locate", "--config", str(cfgp), "--scenario", str(sc_dir),
                            "--no-plots", "--run-id", variant], cwd=code, capture_output=True, text=True,
                           timeout=unit["timeout_s"])
    except subprocess.TimeoutExpired:
        row.update(outcome="timeout", status="none", failure="timeout", seconds=unit["timeout_s"])
        return row
    secs = _time.perf_counter() - t0
    rj = tmp / "out" / variant / "run.json"
    row["seconds"] = secs
    if r.returncode != 0 and "no events detected" in (r.stderr or ""):
        # same row as the in-process arms when run_locate returns None (an abstention, not an error)
        row.update(outcome="abstained", status="none", failure="survival:none", n_candidates=0, n_events=0)
        shutil_rmtree(tmp / "out")
        return row
    if r.returncode != 0 or not rj.exists():
        row.update(outcome="error", status="none", error=(r.stderr or "")[-500:])
        return row
    info = json.loads(rj.read_text())
    te = info.get("truth_eval") or {}
    ep = ev_.EvalParams.from_dict(section(load_config(unit["config_path"]), "evaluate"))
    status = (info.get("ambiguity") or {}).get("status", "none") if info["survivors"]["silence"] else "none"
    err = te.get("rank1_error_m")
    row.update(outcome=ev_.outcome(status, err, None, ep), status=status, rank1_error_m=err,
               true_cell_rank=te.get("rank"), true_cell_reached=te.get("true_cell_reached"),
               legacy_correct=te.get("correct"), survivors=info.get("survivors"),
               rank1_dt_error_s=te.get("rank1_dt_error_s"), n_candidates=info["survivors"]["silence"])
    shutil_rmtree(tmp / "out")
    return row


def shutil_rmtree(p: Path) -> None:
    import shutil

    shutil.rmtree(p, ignore_errors=True)


def variant_fits(cache: dict, vcfg: dict, base_cfg: dict, x, fs: float, keep: Path) -> list:
    """Fitted events under the variant's own detection config, run once per distinct detection config of a unit
    (``cache``: events file name -> fits) and kept as keep/<events_file_name>."""
    from doppler import export

    name = events_file_name(vcfg, base_cfg)
    if name not in cache:
        _, cache[name] = run_events(x, fs, vcfg, speed_of_sound_from(vcfg))
        export.write_events(cache[name], keep / name, None)
    return cache[name]


def _ablate_worker(unit: dict, q) -> None:
    """Process one (scenario, config, seed): synthesise once, detect once per distinct detection config
    (variant_fits), run every variant in-process."""
    import time as _time
    import traceback

    from doppler import export

    try:
        cfg = load_config(unit["config_path"])
        _, region = load_region(cfg, None)
        _, radius = region_frame(region)
        dur, times = ABLATION_SCENARIOS[unit["scenario"]]
        sp = dict(cfg.get("synth") or {})
        sp.update(seed=unit["seed"], duration_s=dur, event_rx_times_s=list(times), event_classes=None,
                  audible_slant_m=section(cfg, "cascade").get("audible_slant_m"), region_radius_m=radius)
        sp.setdefault("mic_height_m", section(cfg, "mic").get("height_agl_m", 1.5))
        params = synth.SynthParams.from_dict(sp)
        rp = realism_for(cfg, unit["config"])
        sc, _, _ = build_synth_scenario(cfg, region, params, rp, params.n_cascade_decoys)
        sc_dir = Path(unit["tmp_dir"]) / "scenario"
        export.write_scenario(sc, sc_dir)
        keep = Path(unit["keep_dir"])
        keep.mkdir(parents=True, exist_ok=True)
        (keep / "truth.json").write_bytes((sc_dir / "truth.json").read_bytes())
        x, fs = sf.read(sc_dir / "audio.flac", always_2d=False)   # exactly what the CLI would analyse
        xm = audio.mix_to_mono(x)
        fits_by_file: dict[str, list] = {}
        variant_fits(fits_by_file, cfg, cfg, xm, fs, keep)       # events.parquet: the base detection, always kept
        inp = LocateInputs(str(sc_dir / "audio.flac"), str(sc_dir / "tracks.parquet"),
                           str(sc_dir / "aircraft_db.csv"), str(sc_dir / "truth.json"), write_grid=False)
        for variant in unit["variants"]:
            t0 = _time.perf_counter()
            if variant.endswith("-asdesigned"):
                q.put(_asdesigned_row(unit, variant, sc_dir, Path(unit["tmp_dir"])))
                continue
            vcfg = apply_variant(cfg, variant)
            res = run_locate(vcfg, inp, fits=variant_fits(fits_by_file, vcfg, cfg, xm, fs, keep), audio_data=(xm, fs))
            if res is None:
                q.put({"scenario": unit["scenario"], "config": unit["config"], "seed": unit["seed"],
                       "variant": variant, "git": unit["git"], "detection_sha256": detection_key(vcfg),
                       "outcome": "abstained", "status": "none",
                       "failure": "survival:none", "n_candidates": 0, "n_events": 0,
                       "seconds": _time.perf_counter() - t0})
                continue
            q.put(ablate_row(unit, variant, res, _time.perf_counter() - t0))
        q.put({"_done": True, "_failing": None})
    except Exception:
        q.put({"_error": traceback.format_exc()[-2000:]})


def _unit_tag(scenario: str, config: str, seed: int) -> str:
    """Directory name of an ablation unit under <out>/tmp and <out>/keep."""
    return f"{scenario}_{config.replace(':', '-').replace('=', '').replace(';', '_')}_s{seed}"


def cmd_ablate(args) -> int:
    """Paired leave-one-group-out ablation (P0.6). Rows append to <out>/rows.jsonl (resumable)."""
    import multiprocessing as mp
    import queue as _queue
    import shutil
    import time as _time

    out = Path(args.out)
    if args.summarize:
        return ablate_summary(out, args.config)
    cfg = load_config(args.config)
    plan = ablation_plan(args.plan)
    if args.limit:
        plan = plan[:args.limit]
    variants = args.variants.split(",")
    out.mkdir(parents=True, exist_ok=True)
    rows_p = out / "rows.jsonl"
    done = set()
    if rows_p.exists():
        kept = []
        for line in rows_p.read_text().splitlines():
            r = json.loads(line)
            if r.get("outcome") == "error":        # failed rows are retried on resume
                continue
            kept.append(line)
            done.add((r["scenario"], r["config"], r["seed"], r["variant"]))
        rows_p.write_text("".join(x + "\n" for x in kept))
    git = _git_state()
    if git.get("dirty") and not args.allow_dirty:
        print("refusing to run on a dirty working tree (rows record the git commit); commit or pass --allow-dirty",
              file=sys.stderr)
        return 2
    (out / "manifest.json").write_text(json.dumps({
        "plan": args.plan, "variants": variants, "git": git, "config": args.config,
        "config_sha256": file_sha256(args.config), "timeout_s": args.timeout_s,
        "started_utc": pd.Timestamp.now(tz="UTC").isoformat(), "n_units": len(plan)}, indent=1))
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
        os.environ.setdefault(k, "1")
    if args.asdesigned_dir:
        args.asdesigned_dir = str(Path(args.asdesigned_dir).resolve())
    args.config = str(Path(args.config).resolve())
    ctx = mp.get_context("spawn")
    pending = []
    for sc, conf, seed in plan:
        todo = [v for v in variants if (sc, conf, seed, v) not in done]
        if todo:
            pending.append({"scenario": sc, "config": conf, "seed": seed, "variants": todo})
    print(f"ablation {args.plan}: {len(pending)} units to run ({len(plan)} in plan), variants {variants}, "
          f"{args.jobs} jobs, timeout {args.timeout_s:g} s per variant")
    active: list[dict] = []
    n_rows = 0
    T0 = t_print = _time.perf_counter()

    def start(unit):
        tag = _unit_tag(unit["scenario"], unit["config"], unit["seed"])
        unit = {**unit, "git": git, "config_path": args.config, "timeout_s": args.timeout_s,
                "tmp_dir": str((out / "tmp" / tag).resolve()), "keep_dir": str((out / "keep" / tag).resolve()),
                "asdesigned_dir": args.asdesigned_dir}
        Path(unit["tmp_dir"]).mkdir(parents=True, exist_ok=True)
        q = ctx.Queue()
        pr = ctx.Process(target=_ablate_worker, args=(unit, q), daemon=True)
        pr.start()
        active.append({"unit": unit, "q": q, "proc": pr, "t_last": _time.perf_counter(), "got": [],
                       "t_start": _time.perf_counter()})

    def finish(a, rows):
        nonlocal n_rows
        with open(rows_p, "a") as fh:
            for r in rows:
                fh.write(json.dumps(r, default=_json_safe) + "\n")
        n_rows += len(rows)
        failing = any(r.get("outcome") != "correct" for r in rows)
        keep = Path(a["unit"]["keep_dir"])
        sc_tracks = Path(a["unit"]["tmp_dir"]) / "scenario" / "tracks.parquet"
        if failing and sc_tracks.exists() and keep.exists():
            shutil.copy2(sc_tracks, keep / "tracks.parquet")
        shutil.rmtree(a["unit"]["tmp_dir"], ignore_errors=True)

    while pending or active:
        free_gb = shutil.disk_usage(out).free / 1e9
        if free_gb < args.min_free_gb:
            print(f"stopping: {free_gb:.1f} GB free < {args.min_free_gb} GB", file=sys.stderr)
            for a in active:
                a["proc"].terminate()
            return 3
        while pending and len(active) < args.jobs:
            start(pending.pop(0))
        for a in list(active):
            try:
                while True:
                    msg = a["q"].get_nowait()
                    a["t_last"] = _time.perf_counter()
                    if "_error" in msg:
                        u = a["unit"]
                        rest = [v for v in u["variants"] if v not in {r["variant"] for r in a["got"]}]
                        a["got"] += [{"scenario": u["scenario"], "config": u["config"], "seed": u["seed"],
                                      "variant": v, "git": git, "outcome": "error", "status": "none",
                                      "error": msg["_error"]} for v in rest]
                        msg = {"_done": True}
                    if "_done" in msg:
                        a["proc"].join(timeout=5)
                        finish(a, a["got"])
                        active.remove(a)
                        break
                    a["got"].append(msg)
            except _queue.Empty:
                pass
            if a in active and _time.perf_counter() - a["t_last"] > args.timeout_s + (
                    120.0 if not a["got"] else 0.0):          # first variant also pays synthesis + detection
                a["proc"].terminate()
                u = a["unit"]
                got_v = {r["variant"] for r in a["got"]}
                rest = [v for v in u["variants"] if v not in got_v]
                a["got"].append({"scenario": u["scenario"], "config": u["config"], "seed": u["seed"],
                                 "variant": rest[0], "git": git, "outcome": "timeout", "status": "none",
                                 "failure": "timeout", "seconds": args.timeout_s})
                finish(a, a["got"])
                active.remove(a)
                if rest[1:]:
                    pending.insert(0, {k: u[k] for k in ("scenario", "config", "seed")} | {"variants": rest[1:]})
        _time.sleep(0.5)
        if _time.perf_counter() - t_print > 60.0:
            t_print = _time.perf_counter()
            print(f"  {n_rows} rows, {len(pending)} units pending, {len(active)} running, "
                  f"{(t_print - T0) / 60:.1f} min", file=sys.stderr, flush=True)
    print(f"done: {n_rows} rows -> {rows_p}")
    shutil.rmtree(out / "tmp", ignore_errors=True)
    return ablate_summary(out, args.config)


def _json_safe(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def _fill_detection(rows: list[dict], out: Path, ep, cfg: dict | None = None) -> tuple[int, int]:
    """Recompute each row's detection counts from its kept truth.json + events.parquet (plan §8.1).

    Rows that already record ``truth_events_detected`` keep their value (a disagreement is counted); rows
    without it (no events, timeouts, the as-designed arms, whose detection code and config are identical to
    the current ones at tag variants-asdesigned) get the recomputed counts. Returns (filled, mismatched).
    With ``cfg``, a variant whose overlay changes detection is read from its own kept events file
    (events_file_name); without it, or for a variant not in cfg, every row reads events.parquet.
    """
    from doppler import evaluate as ev_
    from doppler import export

    cache: dict[tuple[str, str], tuple[int, int] | None] = {}
    names: dict[str, str] = {}
    filled = mismatched = 0
    for r in rows:
        v = r.get("variant", "")
        if v not in names:
            names[v] = (events_file_name(apply_variant(cfg, v), cfg)
                        if cfg is not None and v in (cfg.get("variants") or {}) else "events.parquet")
        tag = (_unit_tag(r["scenario"], r["config"], r["seed"]), names[v])
        if tag not in cache:
            keep = out / "keep" / tag[0]
            if not (keep / "truth.json").exists() or not (keep / tag[1]).exists():
                cache[tag] = None
            else:
                truth = json.loads((keep / "truth.json").read_text())
                ev = export.read_parquet(keep / tag[1], columns=["t_cpa_received_rel_s"])
                t_det = ev["t_cpa_received_rel_s"].to_numpy() if "t_cpa_received_rel_s" in ev else []  # none detected
                m = ev_.match_events(t_det,
                                     [te["t_cpa_received_rel_s"] for te in truth["events"]], ep.match_tol_s)
                cache[tag] = (len(truth["events"]), sum(k is not None for k in m))
        got = cache[tag]
        if got is None:
            continue
        r.setdefault("n_truth_events", got[0])
        if r.get("truth_events_detected") is None:
            r["truth_events_detected"] = got[1]
            filled += 1
        elif r["truth_events_detected"] != got[1]:
            mismatched += 1
    return filled, mismatched


def _paired_tests(rows: list[dict], ep) -> list[dict]:
    """Plan §3.0 paired tests: arm vs arm on all-on C, and each config vs all-on (same variant, same seed)."""
    from doppler import evaluate as ev_

    by = {(r["scenario"], r["config"], r["seed"], r["variant"]): r for r in rows}
    comps = [("C", "all", "V1", "C", "all", "V0"), ("C", "all", "V1", "C", "all", "V1-asdesigned"),
             ("C", "all", "V0", "C", "all", "V0-asdesigned")]
    confs = sorted({r["config"] for r in rows if r["scenario"] == "C" and r["config"] != "all"})
    comps += [("C", c_, v, "C", "all", v) for v in ("V0", "V1") for c_ in confs]
    out = []
    for sa, ca, va, sb, cb, vb in comps:
        seeds = sorted(s for (sc, c_, s, v) in by if (sc, c_, v) == (sa, ca, va) and (sb, cb, s, vb) in by)
        if not seeds:
            continue
        A = [by[(sa, ca, s, va)] for s in seeds]
        B = [by[(sb, cb, s, vb)] for s in seeds]
        tests = {m: ev_.paired_wilcoxon([a.get(m) for a in A], [b.get(m) for b in B])
                 for m in ("rank1_error_m", "true_assignment_group_rank")}
        tests["delta_cost"] = ev_.paired_wilcoxon([ev_.delta_cost(a) for a in A], [ev_.delta_cost(b) for b in B])
        tests["rank1_accuracy_sign"] = ev_.paired_sign([ev_.rank1_ok(a, ep) for a in A], [ev_.rank1_ok(b, ep) for b in B])
        label = f"{va} vs {vb} ({ca})" if ca == cb else f"{va}: {ca} vs {cb}"
        out.append({"label": label, "a": [sa, ca, va], "b": [sb, cb, vb], "seeds": seeds, "tests": tests})
    return out


def ablate_summary(out: Path, config_path: str) -> int:
    """summary.json + summary.md: outcome tiers per (scenario, config, variant), derived fallback arms, and the
    pre-registered re-ordering rule on all-on C."""
    from doppler import evaluate as ev_

    rows = [json.loads(line) for line in (out / "rows.jsonl").read_text().splitlines()]
    cfg_ = load_config(config_path)
    ep = ev_.EvalParams.from_dict(section(cfg_, "evaluate"))
    n_filled, n_mismatch = _fill_detection(rows, out, ep, cfg_)
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["scenario"], r["config"], r["variant"]), []).append(r)
    # derived arms (zero compute): strict first, robust fallback
    by_key = {(r["scenario"], r["config"], r["seed"], r["variant"]): r for r in rows}
    for (sc, conf, seed, v), r in list(by_key.items()):
        if v != "V0" or (sc, conf, seed, "V1") not in by_key:
            continue
        for trig in ("zero_survivors", "absolute_fit"):
            d = ev_.fallback_arm(r, by_key[(sc, conf, seed, "V1")], trig)
            d["variant"] = f"V0->V1 ({trig})"
            groups.setdefault((sc, conf, d["variant"]), []).append(d)
    summ = {f"{sc}|{conf}|{v}": ev_.summarize(rs, ep) for (sc, conf, v), rs in sorted(groups.items())}
    reorder = {v: ("not applicable (the as-designed code reports no failure taxonomy)"
                   if v.endswith("-asdesigned") else ev_.reorder_rule(rs))
               for (sc, conf, v), rs in groups.items() if sc == "C" and conf == "all"}
    paired = _paired_tests(rows, ep)
    (out / "summary.json").write_text(json.dumps({"groups": summ, "reorder_rule_C_all": reorder, "paired": paired,
                                                  "detection_filled_from_keep": n_filled}, indent=1,
                                                 default=_json_safe))
    lines = ["| scenario | config | variant | n | correct | abstained | wrong | confident-wrong | timeout | "
             "precision(unique) | median err m | top failures |", "|" + "---|" * 12]
    for key, s_ in summ.items():
        sc, conf, v = key.split("|")
        top = ", ".join(f"{k} {n}" for k, n in sorted(s_["failures"].items(), key=lambda z: -z[1])[:3])
        pu = s_["precision_unique"]
        med = s_["rank1_error_m_median"]
        lines.append(f"| {sc} | {conf} | {v} | {s_['n']} | {s_['correct']['k']} | {s_['abstained']['k']} | "
                     f"{s_['wrong']['k']} | {s_['confident_wrong']['k']} | {s_['timeout']['k']} | "
                     f"{pu['k']}/{pu['n']} | {'' if med is None else round(med)} | {top} |")
    lines += ["", "Re-ordering rule (all-on C): " + "; ".join(f"{v}: {r_}" for v, r_ in sorted(reorder.items()))]
    # plan §8.1 measures, beside the pre-registered table (which is unchanged)
    lines += ["", "Plan §8.1 measures. rank-1 acc = right pair and within the success radius, status ignored; "
              "answer = unique / n; recall = truth events detected; detection = runs with a truth event never "
              "detected (beside the taxonomy, not in it); 'cond.' = runs where every truth event was detected.", "",
              "| scenario | config | variant | n | rank-1 acc | answer rate | precision(unique) | recall | "
              "detection | cond. n | cond. correct | cond. rank-1 acc | cond. precision(unique) |",
              "|" + "---|" * 13]
    for key, s_ in summ.items():
        sc, conf, v = key.split("|")
        d_, c_ = s_["detection"], s_["conditional_on_detection"]
        rec = f"{d_['detected']}/{d_['truth_events']}" if d_["truth_events"] else "n/a"
        lines.append(f"| {sc} | {conf} | {v} | {s_['n']} | {s_['rank1_accuracy']['k']} | "
                     f"{s_['answer_rate']['k']} | {s_['precision_unique']['k']}/{s_['precision_unique']['n']} | {rec} | "
                     f"{s_['detection_failures']['k']} | {c_['n']} | {c_['correct']['k']} | "
                     f"{c_['rank1_accuracy']['k']} | {c_['precision_unique']['k']}/{c_['precision_unique']['n']} |")
    lines += ["", "Paired tests (plan §3.0): Wilcoxon signed-rank on a − b (pairs with a missing value dropped); "
              "exact sign test on rank-1 accuracy. Δcost is not computable from rows without true-assignment "
              "scores (Stage A).", "",
              "| comparison (a vs b) | metric | n | n≠0 | median a−b | p |", "|" + "---|" * 6]
    for p_ in paired:
        for m, t_ in p_["tests"].items():
            if m == "rank1_accuracy_sign":
                lines.append(f"| {p_['label']} | rank-1 acc (a only / b only) | {t_['n']} | "
                             f"{t_['a_only']}/{t_['b_only']} | | {t_['p']:.3g} |")
            else:
                lines.append(f"| {p_['label']} | {m} | {t_['n']} | {t_['n_nonzero']} | {t_['median_diff']:.3g} | "
                             f"{t_['p']:.3g} |")
    if n_filled:
        lines += ["", f"Detection counts for {n_filled} rows (no-event, timeout and as-designed rows) were recomputed "
                  f"from their kept truth.json + events.parquet ({n_mismatch} disagreed with a recorded count)."]
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
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

    j = sub.add_parser("adsblol", help="crop an adsb.lol daily archive (split tar parts) to a region and window")
    j.add_argument("parts", nargs="+", help="the day's archive parts, e.g. v2026.11.04-planes-readsb-prod-0.tar.a*, "
                   "or - to read the whole tar from stdin")
    j.add_argument("--config", default="config.yaml")
    j.add_argument("--region", help="search region name (default region.name)")
    j.add_argument("--start", required=True, help="window start, ISO 8601 (naive = UTC)")
    j.add_argument("--end", required=True, help="window end, ISO 8601 (naive = UTC)")
    j.add_argument("--out", required=True, help="track Parquet to write (data/ is gitignored)")
    j.set_defaults(func=cmd_adsblol)

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
    l.add_argument("--events", help="events file from `cli.py events --out X.parquet` instead of detecting "
                                    "(its provenance is checked; a mismatch is recorded as a manual input)")
    l.add_argument("--variant", help="named design variant from config.yaml variants: (e.g. V0 strict, V1 robust)")
    l.add_argument("--null-test", dest="null_test", action="store_true",
                   help="report-only time-shift null tests (sets diagnostics.null_test)")
    l.add_argument("--commit-result", dest="commit_result", action="store_true",
                   help="record a SHA-256 commitment of the top candidates (sets diagnostics.commit_result)")
    l.set_defaults(func=cmd_locate)

    vr = sub.add_parser("verify-result", help="check a run's saved candidates against its result commitment")
    vr.add_argument("run", help="run directory (run.json + results.gpkg)")
    vr.add_argument("--sha256", help="hash recorded elsewhere (e.g. published before the truth was consulted)")
    vr.set_defaults(func=cmd_verify_result)

    a = sub.add_parser("ablate", help="paired leave-one-group-out ablation (rows.jsonl + summary), disk-safe")
    a.add_argument("--config", default="config.yaml")
    a.add_argument("--plan", default="pilot", help="pilot | stageA | stageA_ext | stageB")
    a.add_argument("--variants", default="V0,V1", help="comma list: V0, V1 (config variants), "
                                                          "V0-asdesigned, V1-asdesigned (needs --asdesigned-dir)")
    a.add_argument("--out", default="outputs/ablation/pilot")
    a.add_argument("--jobs", type=int, default=3)
    a.add_argument("--timeout-s", dest="timeout_s", type=float, default=900.0, help="wall-clock cap per variant run")
    a.add_argument("--min-free-gb", dest="min_free_gb", type=float, default=3.0)
    a.add_argument("--limit", type=int, help="first N plan rows only (smoke tests)")
    a.add_argument("--asdesigned-dir", dest="asdesigned_dir",
                   help="git worktree at tag variants-asdesigned (git worktree add <dir> variants-asdesigned)")
    a.add_argument("--allow-dirty", dest="allow_dirty", action="store_true")
    a.add_argument("--summarize", action="store_true", help="only rebuild summary.json / summary.md from rows.jsonl")
    a.set_defaults(func=cmd_ablate)

    k = sub.add_parser("clean", help="prune old runs in outputs/ (keep the newest N)")
    k.add_argument("--config", default="config.yaml")
    k.add_argument("--keep", type=int)
    k.set_defaults(func=cmd_clean)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
