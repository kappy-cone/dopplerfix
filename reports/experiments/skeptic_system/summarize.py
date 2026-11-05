"""Summarise the realistic sweep: per run, true-cell stage/rank/error/ambiguity and per-event fit error vs tolerance."""
import json, glob, os
import numpy as np

S = "<scratch>/skeptic_system"
rows = []
for f in sorted(glob.glob(S + "/runs/*/run.json")):
    d = json.load(open(f))
    name = os.path.basename(os.path.dirname(f))
    te = d["truth_eval"] or {}
    tr = json.load(open(d["inputs"]["truth"]))
    clock = (tr.get("realism") or {}).get("clock_error_s", 0.0) or 0.0
    zs = []
    for t in tr["events"]:
        ev = min(d["events"], key=lambda e: abs(e["t_rx_rel_s"] - t["t_cpa_received_rel_s"]))
        if abs(ev["t_rx_rel_s"] - t["t_cpa_received_rel_s"]) > 10:
            zs.append("MISSED"); continue
        tol = ev["tolerances"]
        zv = (ev["v_mps"] - t["v_mps"]) / tol["v_mps"]
        zd = (ev["d_m"] - t["d_m"]) / (tol["d_core_m"] + tol["d_grid_m"])
        zs.append(f"zv{zv:+.2f} zd{zd:+.2f} dd{ev['d_m'] - t['d_m']:+.0f}m {ev['class'][0]}")
    s = d["survivors"]
    rows.append((name, f"ev={len(d['events'])} truth={len(tr['events'])} | surv {s['cpa']}/{s['offset']}/{s['silence']} | "
                       f"true reached={te.get('true_cell_reached')} rank={te.get('rank')} "
                       f"err1={te.get('rank1_error_m') and round(te['rank1_error_m'])} amb={te.get('ambiguity')} "
                       f"inTied={te.get('true_cell_in_tied_set')} correct={te.get('correct')} "
                       f"assignOK={te.get('true_cell_assignment_correct')} | " + " ; ".join(zs)))
for n, r in rows:
    print(f"{n:12s} {r}")
