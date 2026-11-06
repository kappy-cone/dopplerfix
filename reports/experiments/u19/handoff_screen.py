"""U19 task 4: screen of arm h (seed-to-ridge hand-off), detection only. Rules arms out; never a run claim.

Registered in reports/apriori_registry.md ("U19 task 4: arm h") before this screen. Per unit (scenario rebuilt
exactly as `cli.py ablate` does; same audio for every arm, so paired by audio), per arm, `cli.run_events` with
the arm's config overlay on the current default (arm b on):
- base: current default; h1: ridge.handoff_harmonics; h2: ridge.handoff_continue; h12: both.
Per truth event: matched fit (evaluate.match_events, match_tol_s) and its |Δv| / v, |Δd| / d. Per run: spurious
fits (unmatched), and of those the extra fits within match_tol_s of a truth CPA (arm b's V1 loss mechanism),
plus detection wall time.

    .venv/bin/python reports/experiments/u19/handoff_screen.py --out <scratch>/handoff.jsonl --jobs 14
    .venv/bin/python reports/experiments/u19/handoff_screen.py --summarize <scratch>/handoff.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np  # noqa: E402

from screen import _seeds, scenario_audio  # noqa: E402

from cli import deep_merge, detection_key, run_events, section, speed_of_sound_from  # noqa: E402
from doppler import evaluate as ev  # noqa: E402

CONFIGS = "clean,all,loo:path,loo:levels,loo:scene,sub:reflection"
ARMS = {
    "base": {},
    "h1": {"ridge": {"handoff_harmonics": True}},
    "h2": {"ridge": {"handoff_continue": True}},
    "h12": {"ridge": {"handoff_harmonics": True, "handoff_continue": True}},
}


def one(unit):
    conf, seed, arms, scenario = unit
    cfg, sc, truth, xm, fs = scenario_audio(conf, seed, scenario)
    tol = float(section(cfg, "evaluate").get("match_tol_s", 10.0))
    t_true = [te["t_cpa_received_rel_s"] for te in truth["events"]]
    out = []
    for arm in arms:
        acfg = deep_merge(cfg, ARMS[arm])
        t0 = time.perf_counter()
        _, fits = run_events(xm, fs, acfg, speed_of_sound_from(acfg))
        secs = time.perf_counter() - t0
        t_fit = [e.t_cpa_received_s for e in fits]
        m = ev.match_events(t_fit, t_true, tol)
        events = []
        for te, k in zip(truth["events"], m):
            row = {"detected": k is not None}
            if k is not None:
                e = fits[k]
                row.update(dv_rel=abs(e.v_mps / te["v_mps"] - 1), dd_rel=abs(e.d_m / te["d_m"] - 1),
                           dt_s=e.t_cpa_received_s - te["t_cpa_received_rel_s"], f0_hz=e.f0_hz)
            events.append(row)
        used = {k for k in m if k is not None}
        unmatched = [t for i, t in enumerate(t_fit) if i not in used]
        near = sum(any(abs(t - u) < tol for u in t_true) for t in unmatched)
        out.append({"config": conf, "seed": seed, "arm": arm, "scenario": scenario, "detection_sha256": detection_key(acfg),
                    "n_fits": len(fits), "spurious": len(unmatched), "near_cpa_extra": near, "seconds": secs,
                    "events": events})
    return out


def summarize(path: str) -> int:
    from scipy.stats import binomtest

    rows = [json.loads(line) for line in open(path)]
    by = {(r["config"], r["seed"], r["arm"]): r for r in rows}
    configs = list(dict.fromkeys(r["config"] for r in rows))
    arms = list(dict.fromkeys(r["arm"] for r in rows))
    print(f"rows {len(rows)}; commits {sorted({r.get('git') for r in rows})}")
    print("\n| config | arm | recall | gained / lost vs base (p) | spurious | near-CPA extra | median |Δv|/v | "
          "median |Δd|/d | detection s / run |")
    print("|---|---|---|---|---|---|---|---|---|")
    for conf in configs + ["pooled"]:
        cs = configs if conf == "pooled" else [conf]
        seeds = sorted({s for (c, s, a) in by if c in cs and a == "base"})
        for arm in arms:
            R = [by[(c, s, arm)] for c in cs for s in seeds if (c, s, arm) in by]
            B = [by[(c, s, "base")] for c in cs for s in seeds if (c, s, arm) in by]
            det = sum(e["detected"] for r in R for e in r["events"])
            n = sum(len(r["events"]) for r in R)
            g = sum(ea["detected"] and not eb["detected"] for r, b in zip(R, B) for ea, eb in zip(r["events"], b["events"]))
            lo = sum(eb["detected"] and not ea["detected"] for r, b in zip(R, B) for ea, eb in zip(r["events"], b["events"]))
            p = binomtest(g, g + lo).pvalue if g + lo else float("nan")
            dv = [e["dv_rel"] for r in R for e in r["events"] if e["detected"]]
            dd = [e["dd_rel"] for r in R for e in r["events"] if e["detected"]]
            gl = "—" if arm == "base" else f"{g} / {lo} ({p:.3g})"
            print(f"| {conf} | {arm} | {det}/{n} | {gl} | {sum(r['spurious'] for r in R)} | "
                  f"{sum(r['near_cpa_extra'] for r in R)} | {np.median(dv):.3f} | {np.median(dd):.3f} | "
                  f"{np.mean([r['seconds'] for r in R]):.1f} |")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out")
    ap.add_argument("--summarize")
    ap.add_argument("--configs", default=CONFIGS)
    ap.add_argument("--seeds", default="0-49")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--scenario", default="C")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args(argv)
    if a.summarize:
        return summarize(a.summarize)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(k, "1")
    git = subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, text=True).stdout.strip()
    arms = a.arms.split(",")
    units = [(c, s, arms, a.scenario) for c in a.configs.split(",") for s in _seeds(a.seeds)]
    with Pool(a.jobs) as pool, open(a.out, "w") as fh:
        for rows in pool.imap_unordered(one, units):
            for r in rows:
                fh.write(json.dumps({**r, "git": git}, default=float) + "\n")
                fh.flush()
    print(f"{len(units)} units x {len(arms)} arms -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
