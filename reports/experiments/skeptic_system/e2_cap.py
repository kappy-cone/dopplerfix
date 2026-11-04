"""E2: max_hypotheses_per_cell truncation order. One cell, N events, m consistent passes per event,
distinct aircraft; the true assignment uses the LAST pass of each event (highest track index)."""
import numpy as np
from common import *


def table(n_ev, m):
    cell, ev, trk, dtc = [], [], [], []
    k = 0
    for e in range(n_ev):
        for j in range(m):
            cell.append(0); ev.append(e); trk.append(k); dtc.append(0.0 + 0.01 * j); k += 1
    n = len(cell)
    z = np.zeros(n)
    pt = cs.PassTable(np.array(cell, np.int64), np.array(ev, np.int16), np.array(trk, np.int32), z, z, z, z,
                      np.array(dtc), np.full(n, 3.0), z, z, np.zeros((n, 3)))
    icao = np.array([f"a{i}" for i in range(k)], object)
    return pt, icao, [e * m + m - 1 for e in range(n_ev)]


prior = cs.DtPrior(0.0, -10.0, 10.0, 60.0)
for unmatched in (1, 0):
    for n_ev, m in ((2, 4), (2, 8), (3, 3), (3, 4), (3, 6), (4, 3)):
        pt, icao, true_tr = table(n_ev, m)
        p = cs.CascadeParams(max_unmatched_events=unmatched, max_hypotheses_per_cell=64)
        keep, hyp = cs.offset_filter(pt, 1, n_ev, icao, prior, p)
        tl = hyp.tracks.tolist()
        full = sum(1 for t in tl if -1 not in t)
        part = len(tl) - full
        print(f"max_unmatched={unmatched} N={n_ev} m={m}: {len(tl)} hypotheses kept ({full} full, {part} partial); "
              f"true assignment present: {true_tr in tl}; any full with true track for event 0: "
              f"{any(t[0] == true_tr[0] and -1 not in t for t in tl)}")
