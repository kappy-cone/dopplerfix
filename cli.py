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
from doppler.geo import ArrayDEM, FlatDEM, LocalFrame


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


def load_dem(cfg: dict):
    """Flat DEM, or the configured raster read into memory (windowed reads arrive with US mode)."""
    d = section(cfg, "dem")
    if not d.get("path"):
        return FlatDEM(section(cfg, "region").get("ground_elev_m", 0.0))
    import rasterio

    with rasterio.open(d["path"]) as ds:
        return ArrayDEM(ds.read(1), ds.transform, ds.crs.to_string(), ds.nodata, d.get("geoid_undulation_m", 0.0))


def load_tracks(cfg: dict, tracks_path: str, registry_path: str | None) -> list[tracks.Track]:
    """Read ADS-B (CSV/Parquet), clean + resample, attach type codes and classes."""
    tp = section(cfg, "tracks")
    df = pd.read_parquet(tracks_path) if str(tracks_path).endswith(".parquet") else pd.read_csv(tracks_path)
    params = tracks.TrackParams.from_dict(tp)
    raw = tracks.tracks_from_frame(df, params, tp.get("columns") or None)
    prepared = tracks.prepare_tracks(raw, params)
    at = section(cfg, "aircraft_types")
    reg_path = registry_path or at.get("registry_path")
    if reg_path and Path(reg_path).exists():
        tt = pd.read_csv(at["type_table_path"]) if at.get("type_table_path") else None
        lookup = aircraft_types.build_lookup(pd.read_csv(reg_path, dtype=str), tt)
        types, cls = aircraft_types.classify_icao24([t.icao24 for t in prepared], lookup)
        for t, ty, cl in zip(prepared, types, cls):
            t.type_code, t.cls = str(ty), int(cl)
    return prepared


def cmd_synth(args) -> int:
    from doppler import export, plot

    cfg = load_config(args.config)
    sp = dict(cfg.get("synth") or {})
    for k in ("seed", "snr_db", "n_tracks", "duration_s"):
        if getattr(args, k) is not None:
            sp[k] = getattr(args, k)
    if args.event_times:
        sp["event_rx_times_s"] = [float(v) for v in args.event_times.split(",")]
        sp.setdefault("event_classes", None)
        if args.event_classes:
            sp["event_classes"] = args.event_classes.split(",")
        elif sp.get("event_classes") and len(sp["event_classes"]) != len(sp["event_rx_times_s"]):
            sp["event_classes"] = None
    elif args.n_tracks is not None:
        sp["event_rx_times_s"] = None  # legacy even spread
    reg = cfg["region"]
    sp.setdefault("audible_slant_m", section(cfg, "cascade").get("audible_slant_m"))
    sp.setdefault("region_radius_m", reg.get("radius_m", 6000.0))
    if sp.get("audible_slant_m") is None:
        sp.pop("audible_slant_m")
    tp = audio.parse_time_prior(cfg.get("time_prior") or {})
    if tp.start_s is None:
        print("synth needs time_prior.mode: known with start_utc (the scenario's true start)", file=sys.stderr)
        return 2
    mic_cfg = cfg.get("mic") or {}
    if "height_agl_m" in mic_cfg:
        sp.setdefault("mic_height_m", mic_cfg["height_agl_m"])
    params = synth.SynthParams.from_dict(sp)
    c = speed_of_sound_from(cfg)
    frame = LocalFrame(reg["center_lat"], reg["center_lon"], reg.get("ground_elev_m", 0.0))
    sc = synth.make_scenario(params, frame, tp.start_s, reg.get("ground_elev_m", 0.0), c)

    out = Path(args.out or f"outputs/synth_seed{params.seed}")
    paths = export.write_scenario(sc, out)
    det, fits = run_events(sc.audio, sc.fs_hz, cfg, c)
    export.write_events(fits, out / "events_fit.json", sc.start_time_s)
    # display spectrogram: coarse, so plotting long clips stays cheap
    disp = audio.compute_spectrogram(sc.audio, sc.fs_hz, audio.SpectrogramParams(8000, 1.0, 0.5, 3900.0))
    plot.plot_scenario(sc, disp, fits, out / "diagnostic.png")

    print(f"hidden mic: {sc.mic_lat:.6f}, {sc.mic_lon:.6f}  (ENU {sc.mic_enu[0]:.0f} E, {sc.mic_enu[1]:.0f} N)")
    print(f"c = {c:.1f} m/s, {len(sc.truth)} flyovers, SNR {params.snr_db:g} dB, clip {params.duration_s:g} s")
    for t, s in zip(sc.truth, sc.event_specs):
        print(f"  truth {s.callsign} {t.icao24} ({s.cls}): rx CPA {t.t_cpa_received_rel_s:7.2f}s  v {t.v_mps:5.1f}"
              f"  d {t.d_m:6.0f}  f0 {t.f0_hz:6.1f}  turn {s.turn_rate_deg_s:+.2f}°/s")
    roles = pd.Series([s.role for s in sc.specs]).value_counts().to_dict()
    failed = sum(1 for d in sc.decoys if not d.icao24)
    print(f"tracks by role: {roles}" + (f"  ({failed} non-event placements infeasible)" if failed else ""))
    print("fitted events:")
    print_fits(fits)
    for k, p in {**paths, "events": out / "events_fit.json", "plot": out / "diagnostic.png"}.items():
        print(f"  wrote {k:8s} {p}")
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


def cmd_locate(args) -> int:
    print("locate: not implemented yet (milestones 3-5: seeding, grid scoring, refinement)", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Acoustic Doppler geolocation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth", help="write a synthetic scenario (audio, tracks, ground truth, diagnostic plot)")
    s.add_argument("--config", default="config.yaml")
    s.add_argument("--out")
    s.add_argument("--seed", type=int)
    s.add_argument("--snr-db", dest="snr_db", type=float)
    s.add_argument("--n-tracks", dest="n_tracks", type=int)
    s.add_argument("--duration-s", dest="duration_s", type=float)
    s.add_argument("--event-times", dest="event_times", help="received CPA times, e.g. 40,960")
    s.add_argument("--event-classes", dest="event_classes", help="e.g. prop,jet")
    s.set_defaults(func=cmd_synth)

    e = sub.add_parser("events", help="detect and fit flyover events in a recording")
    e.add_argument("--config", default="config.yaml")
    e.add_argument("--audio", required=True)
    e.add_argument("--sidecar", help="time-prior JSON (default: <audio>.json)")
    e.add_argument("--out", help="write events JSON (includes ridge points)")
    e.set_defaults(func=cmd_events)

    t = sub.add_parser("tracks", help="ingest, clean and classify ADS-B tracks; print a summary")
    t.add_argument("--config", default="config.yaml")
    t.add_argument("--tracks", required=True)
    t.add_argument("--registry", help="aircraft registry CSV (default: aircraft_types.registry_path)")
    t.set_defaults(func=cmd_tracks)

    l = sub.add_parser("locate", help="estimate the recording location (not yet implemented)")
    l.set_defaults(func=cmd_locate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
