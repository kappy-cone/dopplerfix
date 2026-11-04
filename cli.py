"""Command line: synth | events | locate.

    python cli.py synth  --config config.yaml --out outputs/synth_seed0 [--seed 0] [--snr-db 10]
    python cli.py events --config config.yaml --audio outputs/synth_seed0/audio.wav [--out events.json]
    python cli.py locate ...   (milestone 4)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import soundfile as sf
import yaml

from doppler import audio, detect, fit, physics, ridge, synth
from doppler.geo import LocalFrame


def load_config(path: str | Path) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def speed_of_sound_from(cfg: dict) -> float:
    t = (cfg.get("environment") or {}).get("temperature_c")
    return physics.speed_of_sound(t) if t is not None else physics.DEFAULT_C


def run_events(x, fs, cfg: dict, c: float):
    """Spectrogram -> detection -> ridge + fit, using config sections."""
    spec = audio.compute_spectrogram(x, fs, audio.SpectrogramParams.from_dict(cfg.get("spectrogram") or {}))
    windows = detect.detect_events(spec, detect.DetectParams.from_dict(cfg.get("detect") or {}))
    fits = fit.characterize_events(spec, windows, ridge.RidgeParams.from_dict(cfg.get("ridge") or {}),
                                   fit.FitParams.from_dict(cfg.get("fit") or {}), c)
    return spec, windows, fits


def print_fits(fits) -> None:
    print(f"{'#':>2} {'t_cpa_rx':>9} {'f0 Hz':>7} {'v m/s':>12} {'d m':>12} {'t0 s':>14} {'rms':>5} sub")
    for i, e in enumerate(fits):
        s = e.sigma
        print(f"{i:2d} {e.t_cpa_received_s:9.2f} {e.f0_hz:7.1f} {e.v_mps:6.1f}±{s['v_mps']:<5.1f}"
              f" {e.d_m:6.0f}±{s['d_m']:<5.0f} {e.t0_s:8.2f}±{s['t0_s']:<5.2f} {e.rms_hz:5.2f} {e.ridge.subharmonic}")


def cmd_synth(args) -> int:
    from doppler import export, plot

    cfg = load_config(args.config)
    sp = dict(cfg.get("synth") or {})
    for k in ("seed", "snr_db", "n_tracks", "duration_s"):
        if getattr(args, k) is not None:
            sp[k] = getattr(args, k)
    reg = cfg["region"]
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
    spec, _, fits = run_events(sc.audio, sc.fs_hz, cfg, c)
    export.write_events(fits, out / "events_fit.json", sc.start_time_s)
    plot.plot_scenario(sc, spec, fits, out / "diagnostic.png")

    print(f"hidden mic: {sc.mic_lat:.6f}, {sc.mic_lon:.6f}  (ENU {sc.mic_enu[0]:.0f} E, {sc.mic_enu[1]:.0f} N)")
    print(f"c = {c:.1f} m/s, {len(sc.truth)} flyovers, SNR {params.snr_db:g} dB")
    for t, s in zip(sc.truth, sc.specs):
        print(f"  truth {s.callsign} {t.icao24}: t0 {t.t0_rel_s:7.2f}s  v {t.v_mps:5.1f}  d {t.d_m:6.0f}"
              f"  f0 {t.f0_hz:6.1f}  turn {s.turn_rate_deg_s:+.2f}°/s")
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
    _, windows, fits = run_events(audio.mix_to_mono(x), fs, cfg, c)
    print(f"time prior: {tp.mode}; {len(windows)} windows, {len(fits)} events")
    print_fits(fits)
    if args.out:
        export.write_events(fits, Path(args.out), tp.start_s)
        print(f"wrote {args.out}")
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
    s.set_defaults(func=cmd_synth)

    e = sub.add_parser("events", help="detect and fit flyover events in a recording")
    e.add_argument("--config", default="config.yaml")
    e.add_argument("--audio", required=True)
    e.add_argument("--sidecar", help="time-prior JSON (default: <audio>.json)")
    e.add_argument("--out", help="write events JSON (includes ridge points)")
    e.set_defaults(func=cmd_events)

    l = sub.add_parser("locate", help="estimate the recording location (not yet implemented)")
    l.set_defaults(func=cmd_locate)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
