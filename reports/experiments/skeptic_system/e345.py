"""E3 explainer leak, E4 ADS-B gap at CPA, E5 slow (filtered) audible aircraft next to the true mic."""
import numpy as np
from common import *

DUR = 120.0
t_rel = np.arange(-200, DUR + 100, 1.0)
mic = np.array([0.0, 0.0, 1.5])
W = np.array([6000.0, 0.0, 1.5])


def cell_state(res, spec, xyz):
    for r in res:
        if not len(r.tile):
            continue
        d = np.linalg.norm(r.tile.xyz - xyz, axis=1)
        h = int(np.argmin(d))
        if d[h] <= spec.cell_half_diag_m + 1:
            q = np.flatnonzero(r.hypotheses.cell == h)
            return dict(cpa=bool(r.cpa[h]), offset=bool(r.offset[h]), silence=bool(r.silence[h]),
                        n_audible=int(r.n_audible[h]), n_unexplained=int(r.n_unexplained[h]),
                        pen=round(float(r.silence_penalty[h]), 2), n_hyp=len(q),
                        hyps=[tuple(int(k) for k in r.hypotheses.tracks[qq]) for qq in q])
    return None


def tx(enu_pass, hdg, v, alt, trx, anchor, name, **kw):
    d = np.linalg.norm(np.r_[enu_pass, alt] - anchor)
    return straight(name, t_rel, enu_pass, hdg, v, alt, trx - d / C, **kw)


# ---------------- E3: two aircraft consistent with event 1 at a wrong cell W ----------------
A, eA, _ = tx(np.array([500.0, 0.0]), 0.0, 70.0, 600.0, 30.0, mic, "A")         # true event 1
Cc, eC, _ = tx(np.array([400.0, 0.0]), 180.0, 65.0, 700.0, 90.0, mic, "C")      # true event 2
X, eX, _ = tx(W[:2] + [500.0, 0.0], 0.0, 70.0, 600.0, 30.0, W, "X")             # at W: like event 1
B, eB, _ = tx(W[:2] + [-500.0, 0.0], 0.0, 72.0, 650.0, 31.0, W, "B")            # second aircraft, also like event 1
Y, eY, _ = tx(W[:2] + [400.0, 0.0], 0.0, 65.0, 700.0, 90.0, W, "Y")             # at W: like event 2
for nm, e in (("X", eX), ("B", eB), ("Y", eY)):
    r = np.linalg.norm(e - mic, axis=1); m = (t_rel + r / C >= 0) & (t_rel + r / C <= DUR)
    print(f"E3 {nm}: min slant to true mic in clip {r[m].min():.0f} m")
ev = [truth_event(0, eA, t_rel, mic), truth_event(1, eC, t_rel, mic)]
for label, trs in (("with B", [A, Cc, X, B, Y]), ("without B", [A, Cc, X, Y])):
    prep = prepare(trs)
    res, ts, spec, prior = run_cascade(ev, prep, params(**STRICT), DUR, radius_m=8000.0)
    sW = cell_state(res, spec, FRAME.enu_to_ecef(W))
    sT = cell_state(res, spec, FRAME.enu_to_ecef(mic))
    names = list(ts.icao24)
    if sW:
        sW["hyps"] = [tuple(names[k] if k >= 0 else "-" for k in h) for h in sW["hyps"]]
    print(f"E3 strict, {label}: wrong cell W -> {sW}")
    print(f"E3 strict, {label}: true cell -> { {k: v for k, v in sT.items() if k != 'hyps'} if sT else None}")

# ---------------- E4: ADS-B gap spanning the CPA of an event track ----------------
for gap in (None, (20.0, 35.0), (15.0, 40.0), (5.0, 50.0)):
    A2, eA2, _ = tx(np.array([500.0, 0.0]), 0.0, 70.0, 600.0, 30.0, mic, "A", gap=gap)
    prep = prepare([A2, Cc])
    for lab, p in (("strict", params(**STRICT)), ("robust", params())):
        res, ts, spec, prior = run_cascade(ev, prep, p, DUR, radius_m=3000.0)
        sT = cell_state(res, spec, FRAME.enu_to_ecef(mic))
        names = list(ts.icao24)
        hy = [tuple(names[k] if k >= 0 else "-" for k in h) for h in sT["hyps"]] if sT else None
        print(f"E4 gap(emission t, CPA emission ~{30 - 781 / C:.1f}s)={gap} {lab}: true cell "
              f"cpa={sT and sT['cpa']} offset={sT and sT['offset']} silence={sT and sT['silence']} "
              f"unexpl={sT and sT['n_unexplained']} hyps={hy}")

# ---------------- E5: slow audible aircraft (helicopter, 8 m/s, 150 m AGL, 1.2 km away) ----------------
for dist in (1200.0, 500.0):
    H, eH, _ = straight("H", t_rel, np.array([dist, 0.0]), 90.0, 8.0, 150.0, 60.0)
    prep = prepare([A, Cc, H])
    for lab, p in (("strict", params(**STRICT)), ("robust", params())):
        res, ts, spec, prior = run_cascade(ev, prep, p, DUR, radius_m=3000.0)
        sT = cell_state(res, spec, FRAME.enu_to_ecef(mic))
        print(f"E5 helicopter at {dist:.0f} m {lab}: true cell silence={sT['silence']} audible={sT['n_audible']} "
              f"unexplained={sT['n_unexplained']} penalty={sT['pen']} nats")
