"""U19 detection screen (plan §8.2 / §8.4): event detection only, no cascade, paired by seed.

Each unit rebuilds the scenario exactly as `cli.py ablate` does (same synthesis, same FLAC round trip), then
runs `cli.run_events` once per detection arm on the same audio. Per truth event it records whether a detection
window covers its received CPA time, the peak of the normalised detection statistic within ±match_tol_s, and
whether a fitted event matches it (the Stage A definition: nearest fitted event within match_tol_s). Fitted
events matching no truth event are spurious.

Run from the repo root:
    .venv/bin/python reports/experiments/u19/screen.py --out <scratch>/u19_screen.jsonl --jobs 8 \
        --configs clean,all,loo:path,loo:levels,loo:scene,sub:reflection --seeds 0-49 --arms base
Screens only rule ideas out (plan §8.4); the decision run is a separate `cli.py ablate` from main.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

import cli  # noqa: E402
from cli import (ABLATION_SCENARIOS, audio, build_synth_scenario, deep_merge, load_config, load_region,  # noqa: E402
                 realism_for, region_frame, run_events, section, speed_of_sound_from, synth)

CONFIG = "config.yaml"
# Detection arms: config overlays (variant names once they exist in config.yaml; "base" = the current detection).
ARMS: dict[str, dict] = {
    "base": {},
    "a": {"detect": {"harmonic_stat": True}},                 # harmonic-summed row
    "b": {"detect": {"long_window_s": 3.0}},                  # long-window rows
    "c": {"detect": {"low_band_hz": [20.0, 100.0]}},          # low-band row
    "abc": {"detect": {"harmonic_stat": True, "long_window_s": 3.0, "low_band_hz": [20.0, 100.0]}},
}


def arm_cfg(cfg: dict, arm: str) -> dict:
    over = ARMS.get(arm)
    if over is None:                       # a named config variant
        return cli.apply_variant(cfg, arm)
    return deep_merge(cfg, over)


def one(unit):
    conf, seed, arms, scenario = unit
    from doppler import export

    cfg = load_config(CONFIG)
    _, region = load_region(cfg, None)
    _, radius = region_frame(region)
    dur, times = ABLATION_SCENARIOS[scenario]
    sp = dict(cfg.get("synth") or {})
    sp.update(seed=seed, duration_s=dur, event_rx_times_s=list(times), event_classes=None,
              audible_slant_m=section(cfg, "cascade").get("audible_slant_m"), region_radius_m=radius)
    sp.setdefault("mic_height_m", section(cfg, "mic").get("height_agl_m", 1.5))
    params = synth.SynthParams.from_dict(sp)
    sc, _, _ = build_synth_scenario(cfg, region, params, realism_for(cfg, conf), params.n_cascade_decoys)
    tmp = Path(tempfile.mkdtemp(prefix="u19_"))
    try:
        export.write_scenario(sc, tmp)
        truth = json.loads((tmp / "truth.json").read_text())
        x, fs = sf.read(tmp / "audio.flac", always_2d=False)   # exactly what the CLI would analyse
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    xm = audio.mix_to_mono(x)
    tol = float(section(cfg, "evaluate").get("match_tol_s", 10.0))
    info = {a["icao24"]: a for a in truth.get("aircraft", [])} if isinstance(truth.get("aircraft"), list) else \
        (truth.get("aircraft") or {})
    out = []
    for arm in arms:
        acfg = arm_cfg(cfg, arm)
        c = speed_of_sound_from(acfg)
        det, fits = run_events(xm, fs, acfg, c)
        t_fit = np.array([f.t_cpa_received_s for f in fits], float)
        ev_rows, matched = [], set()
        for te in truth["events"]:
            t = te["t_cpa_received_rel_s"]
            k = int(np.argmin(np.abs(t_fit - t))) if len(t_fit) else None
            ok = k is not None and abs(t_fit[k] - t) < tol
            if ok:
                matched.add(k)
            near = np.abs(det.t_s - t) <= tol
            a = info.get(te["icao24"], {})
            ev_rows.append({"icao24": te["icao24"], "t_rx_s": t, "detected": bool(ok),
                            "window": any(w.start_s <= t <= w.end_s for w in det.windows),
                            "stat_peak_db": float(det.stat[near].max()) if near.any() else None,
                            "peak_band_snr_db": a.get("peak_band_snr_db"), "level_db": a.get("level_db_at_ref"),
                            "d_m": te.get("d_m"), "v_mps": te.get("v_mps"), "f0_hz": te.get("f0_hz")})
        out.append({"scenario": scenario, "config": conf, "seed": seed, "arm": arm, "n_windows": len(det.windows),
                    "n_fits": len(fits), "n_truth": len(truth["events"]),
                    "detected": sum(e["detected"] for e in ev_rows),
                    "spurious": len(fits) - len(matched), "events": ev_rows})
    return out


def _seeds(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--configs", default="clean,all,loo:path,loo:levels,loo:scene,sub:reflection")
    ap.add_argument("--seeds", default="0-49")
    ap.add_argument("--arms", default="base")
    ap.add_argument("--scenario", default="C")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args(argv)
    import os
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(k, "1")
    arms = a.arms.split(",")
    units = [(c, s, arms, a.scenario) for c in a.configs.split(",") for s in _seeds(a.seeds)]
    with Pool(a.jobs) as pool, open(a.out, "w") as fh:
        for rows in pool.imap_unordered(one, units):
            for r in rows:
                fh.write(json.dumps(r) + "\n")
                fh.flush()
    print(f"{len(units)} units x {len(arms)} arms -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
