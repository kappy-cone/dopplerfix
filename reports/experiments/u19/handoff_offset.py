"""U19 task 4, step 1 (reporting only, no knob): how far the Hough seed curve sits from the line it should guide.

D1 found that "window, no fit" misses are lost when the guided ridge around the Hough seed comes back short
(`guided_short`) and the peel breaks out of the window. Its "likely mechanism" is that the seed grid is too
coarse for the guide corridor (ridge.guide_hz, ±3 Hz) on harmonics. The D1 trace did not record the seed's offset;
this script does.

Per unit (scenario rebuilt exactly as `cli.py ablate` does), current default detection, the traced peel of d1.py
(identical fits to fit.characterize_audio, checked per unit). For every prop-band peel attempt that got a Hough seed:
- the truth line it is nearest: the (truth event, k in FAMILY) minimising the median relative offset
  |f_seed / (k·f_truth) − 1| over the frames where the truth curve exists in the window;
- the offset from that line: median and 90th-percentile |Δf| in Hz, and the fraction of frames within guide_hz;
- the seed's parameter errors against that event's truth, in Hough grid steps: log(f0_seed / (k·f0_true)) / dlogf,
  log(v_seed / v_true) / v_step, log(τ_seed / τ_true) / tau_step with τ = d / v, (t0_seed − t0_true) / hough_dt_s;
- the attempt's outcome (guided_short, fit, no_descent) and whether the truth event ended up detected.
The truth curve leaves out source-tone jitter / RPM walk (as d1.py R2), so part of an offset can be the source,
not the seed.

    .venv/bin/python reports/experiments/u19/handoff_offset.py --out <scratch>/offset.jsonl --jobs 14
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np  # noqa: E402

from d1 import FAMILY, characterize_audio, f_truth_at, traced_characterize, truth_curves  # noqa: E402
from screen import _seeds, scenario_audio  # noqa: E402

from cli import event_params, section, speed_of_sound_from  # noqa: E402
from doppler.detect import detect_events_audio  # noqa: E402

CONFIGS = "clean,all,loo:path,loo:levels,loo:scene,sub:reflection"


def nearest_line(t, f_seed, curves):
    """(event index, k, relative offsets per frame) of the truth line nearest the seed curve, or None."""
    best = None
    for i, curve in enumerate(curves):
        ft = f_truth_at(curve, t)
        ok = np.isfinite(ft)
        if ok.sum() < 10:
            continue
        for k in FAMILY:
            r = f_seed[ok] / (k * ft[ok]) - 1.0
            m = float(np.median(np.abs(r)))
            if best is None or m < best[0]:
                best = (m, i, k, ok, ft)
    if best is None:
        return None
    _, i, k, ok, ft = best
    return i, k, ok, ft


def attempt_row(a, curves, truth, detected, rp, c):
    t, f_seed = a["seed_curve"]
    t, f_seed = np.asarray(t), np.asarray(f_seed)
    near = nearest_line(t, f_seed, curves)
    if near is None:
        return {"kind": a["kind"], "win": a["win"], "line": None}
    i, k, ok, ft = near
    df = np.abs(f_seed[ok] - k * ft[ok])
    te = truth["events"][i]
    f0s, vs, ds, t0s = a["seed"]
    v_t, d_t, f0_t = te.get("v_mps"), te.get("d_m"), te.get("f0_hz")
    t0_t = te["t_cpa_received_rel_s"] - d_t / c if d_t is not None else None
    steps = {}
    if v_t and d_t and f0_t:
        steps = {"f0_steps": float(np.log(f0s / (k * f0_t)) / rp.hough_dlogf),
                 "v_steps": float(np.log(vs / v_t) / rp.hough_v_step),
                 "tau_steps": float(np.log((ds / vs) / (d_t / v_t)) / rp.hough_tau_step),
                 "t0_steps": float((t0s - t0_t) / rp.hough_dt_s)}
    return {"kind": a["kind"], "win": a["win"], "event": i, "k": k, "event_detected": bool(detected[i]),
            "n_frames": int(ok.sum()), "median_df_hz": float(np.median(df)), "p90_df_hz": float(np.percentile(df, 90)),
            "median_rel": float(np.median(np.abs(f_seed[ok] / (k * ft[ok]) - 1.0))),
            "frac_within_guide": float(np.mean(df <= rp.guide_hz)), "k_f_mean_hz": float(np.mean(k * ft[ok])),
            **steps}


def one(unit):
    conf, seed, scenario = unit
    t0 = time.time()
    cfg, sc, truth, xm, fs = scenario_audio(conf, seed, scenario)
    tol = float(section(cfg, "evaluate").get("match_tol_s", 10.0))
    c = speed_of_sound_from(cfg)
    p = event_params(cfg)
    det = detect_events_audio(xm, fs, p["spec"], p["detect"], p["classify"].broadband_band_hz, p["spec_jet"])
    fits, log = traced_characterize(xm, fs, det, p, c)
    ref = characterize_audio(xm, fs, det, p["spec"], p["ridge"], p["ridge_jet"], p["fit"], p["classify"], c,
                             jet_spec_params=p["spec_jet"])
    trace_ok = len(fits) == len(ref) and all(
        (x.t_cpa_received_s, x.f0_hz, x.v_mps, x.d_m) == (y.t_cpa_received_s, y.f0_hz, y.v_mps, y.d_m)
        for x, y in zip(fits, ref))
    curves = truth_curves(sc)
    t_fit = np.array([e.t_cpa_received_s for e in fits], float)
    detected = [bool(len(t_fit)) and float(np.min(np.abs(t_fit - te["t_cpa_received_rel_s"]))) < tol
                for te in truth["events"]]
    rows = [attempt_row(a, curves, truth, detected, p["ridge"], c)
            for a in log["attempts"] if a["band"] == "prop" and "seed" in a]
    return {"scenario": scenario, "config": conf, "seed": seed, "trace_ok": trace_ok, "n_truth": len(truth["events"]),
            "detected": detected, "attempts": rows, "wall_s": round(time.time() - t0, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--configs", default=CONFIGS)
    ap.add_argument("--seeds", default="0-49")
    ap.add_argument("--scenario", default="C")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args(argv)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(k, "1")
    units = [(c, s, a.scenario) for c in a.configs.split(",") for s in _seeds(a.seeds)]
    import subprocess
    git = subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, text=True).stdout.strip()
    with Pool(a.jobs) as pool, open(a.out, "w") as fh:
        for row in pool.imap_unordered(one, units):
            fh.write(json.dumps({**row, "git": git}, default=float) + "\n")
            fh.flush()
    print(f"{len(units)} units -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
