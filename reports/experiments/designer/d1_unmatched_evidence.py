"""Re-check system F1 (lexicographic ranking -> confident wrong answer when event 2's aircraft lacks ADS-B),
and test whether (a) an absolute per-event goodness-of-fit check on rank 1 and (b) evidence ranking with an
unmatched-event price would have caught it. Reuses the skeptic's E1 scenario generator (read-only import)."""
import sys, time
sys.path.insert(0, "<scratch>/skeptic_system")
import numpy as np
from common import *
import e1_density as E

def run(K, s):
    trs, events, mic = E.scenario(K, 1000 * K + s)
    no2 = [t for t in trs if t.icao24 != "ev1"]
    p = params()
    prep = prepare(no2)
    res, ts, spec, prior = run_cascade(events, prep, p, E.DUR, radius_m=10000.0)
    mic_xyz = FRAME.enu_to_ecef(np.array([0.0, 0.0, 1.5]))
    cells = []; dist = {}
    for r in res:
        if not len(r.tile): continue
        dd = np.linalg.norm(r.tile.xyz - mic_xyz, axis=1)
        for h in np.flatnonzero(r.silence):
            q = np.flatnonzero(r.hypotheses.cell == h)
            cid = int(spec.cell_ids(r.tile.i[h], r.tile.j[h]))
            hyps = [cs.Hypothesis(cid, tuple(int(k) for k in r.hypotheses.tracks[qq]),
                    (float(r.hypotheses.dt_lo_s[qq]), float(r.hypotheses.dt_hi_s[qq]))) for qq in q]
            cells.append((cid, r.tile.xyz[h], hyps, float(r.silence_penalty[h]))); dist[cid] = dd[h]
    sp = score.ScoreParams.from_dict({**CFG["score"], "refine_in_cell": False})
    ridges = score.prepare_ridges(events, sp)
    npts = np.array([len(x.t_s) for x in ridges]); sig = np.array([x.sigma_hz for x in ridges])
    # score every hypothesis of every cell; keep per-cell best full and best partial
    rows = []
    for cid, xyz, hyps, pen in cells:
        for h in hyps:
            sc = score.score_hypothesis(h, ridges, ts, xyz, START, C, sp, (prior.dt_lo_s, prior.dt_hi_s))
            tot = sc.per_event_cost * npts          # per-event Huber sums
            rows.append((cid, sc.n_unmatched, tot, sc.rms_hz, dist[cid], h.assignment))
    full = [r for r in rows if r[1] == 0]; part = [r for r in rows if r[1] == 1]
    bf = min(full, key=lambda r: r[2].sum()) if full else None
    bp = min(part, key=lambda r: r[2].sum()) if part else None
    print(f"K={K} seed={s}: hyps {len(rows)} (full {len(full)}, partial {len(part)}) sigma/event {sig.round(3)} npts {npts}")
    for name, r in (("best FULL", bf), ("best PARTIAL", bp)):
        if r is None: print(f"  {name}: none"); continue
        print(f"  {name}: dist {r[4]:7.0f} m  per-event Huber sums {r[2].round(1)}  rms/sigma {np.round(r[3]/sig,2)}  tracks {[str(ts.icao24[k]) if k>=0 else '-' for k in r[5]]}")
    if bf and bp:
        # evidence ranking: unmatched event priced at k_miss per point (pipeline's 'missing' price = 22.5)
        for price in (2.0, 4.5, 22.5):
            pp = bp[2].sum() + price * npts[np.isnan(bp[3])].sum()
            print(f"   unmatched price {price:5.1f}/pt -> partial total {pp:8.1f} vs full {bf[2].sum():8.1f}: winner {'PARTIAL(true side)' if pp < bf[2].sum() else 'FULL'}")
        # absolute GOF on the full winner: any event rms > 2 sigma (ridge sigma)
        print(f"   abs-GOF flag on full winner (max rms/sigma > 2): {bool(np.nanmax(bf[3]/sig) > 2)}")

if __name__ == "__main__":
    for K, s in [(60, 1), (120, 0), (120, 1)]:
        t0 = time.time(); run(K, s); print(f"   ({time.time()-t0:.0f}s)", flush=True)
