"""Review M2 check: on the skeptic's realistic C sweep (robust and strict, all realism), compare the per-event RMS
residual (Hz) of the rank-1 candidate with that of the true cell. Can an absolute-fit test separate right from
wrong rank 1s under realism, or does it also flag true answers?"""
import json, glob, os
import numpy as np, geopandas as gpd
R = "<scratch>/skeptic_system/runs"
for d in sorted(glob.glob(R + "/*_*")):
    j = json.load(open(d + "/run.json")); te = j.get("truth_eval") or {}
    try:
        c = gpd.read_file(d + "/results.gpkg", layer="candidates")
    except Exception as e:
        print(os.path.basename(d), "no candidates"); continue
    if len(c) == 0:
        print(f"{os.path.basename(d):10s} 0 candidates"); continue
    rc = [k for k in c.columns if k.startswith("rms_e")]
    c = c.sort_values("rank")
    r1 = c.iloc[0][rc].to_numpy(float)
    tid = te.get("true_cell_id"); tr = c[c.cell_id == tid]
    trv = tr.iloc[0][rc].to_numpy(float) if len(tr) else None
    print(f"{os.path.basename(d):10s} correct={te.get('correct')} rank1_err={te.get('rank1_error_m', float('nan')):7.0f} m "
          f"status={te.get('ambiguity')}  rank1 rms {np.round(r1,2)}  true-cell rms {None if trv is None else np.round(trv,2)} (true rank {te.get('rank')})")
