"""E1: wrong-cell survival vs traffic density (truth-derived events, straight tracks, flat ground).

Two prop events at the true mic (t_rx 30 s and 90 s of a 120 s clip). K background props random in a
15 km radius, all >= 1.2 x prop threshold from the mic during the clip (so the true cell is consistent).
Variants: strict (all matched, hard silence) and robust (<=1 unmatched, probabilistic silence).
Case 'noadsb2': event 2's aircraft is absent from ADS-B (UAT / no transponder).
Counts: cells surviving silence with ALL events matched by a hypothesis, far (>1 km) from the mic.
Then locked scoring + lexicographic ranking: where does the true cell rank?
"""
import sys, time
import numpy as np
from common import *

DUR = 120.0
t_rel = np.arange(-200, DUR + 100, 1.0)
rng0 = np.random.default_rng(12345)


def scenario(K, seed):
    rng = np.random.default_rng(seed)
    mic = np.array([0.0, 0.0, 1.5])
    trs, enus = [], []
    ev_specs = []
    for i, trx in enumerate((30.0, 90.0)):
        hdg = rng.uniform(0, 360); v = rng.uniform(50, 80); alt = rng.uniform(300, 900); off = rng.uniform(-900, 900)
        n = np.array([np.cos(np.deg2rad(hdg)), -np.sin(np.deg2rad(hdg))])
        d = np.hypot(alt, off)
        tr, enu, _ = straight(f"ev{i}", t_rel, off * n, hdg, v, alt, trx - d / C)
        trs.append(tr); enus.append(enu); ev_specs.append(trx - d / C)
    nb = 0
    while nb < K:
        p0 = rng.uniform(-15000, 15000, 2)
        if np.hypot(*p0) > 15000:
            continue
        hdg = rng.uniform(0, 360); v = rng.uniform(45, 85); alt = rng.uniform(300, 1500)
        tr, enu, _ = straight(f"bg{nb:03d}", t_rel, p0, hdg, v, alt, rng.uniform(-60, DUR + 60))
        r = np.linalg.norm(enu - mic, axis=1)
        trx = t_rel + r / C
        m = (trx >= -10) & (trx <= DUR + 10)
        if r[m].min() < 1.2 * 3000:
            continue
        trs.append(tr); enus.append(enu); nb += 1
    events = [truth_event(k, enus[k], t_rel, mic, t_near=ev_specs[k], ridge=True, dur=DUR) for k in range(2)]
    return trs, events, mic


def analyse(trs, events, p, label, do_score=True):
    prep = prepare(trs)
    res, ts, spec, prior = run_cascade(events, prep, p, DUR, radius_m=10000.0)
    mic_xyz = FRAME.enu_to_ecef(np.array([0.0, 0.0, 1.5]))
    n_off = n_sil = n_far_full = n_bg_full = 0
    cells = []
    true_id = None
    max_pass = 0
    hyp_cap_hits = 0
    for r in res:
        if not len(r.tile):
            continue
        n_off += int(r.offset.sum()); n_sil += int(r.silence.sum())
        dist = np.linalg.norm(r.tile.xyz - mic_xyz, axis=1)
        if len(r.passes):
            key = r.passes.cell.astype(np.int64) * 10 + r.passes.event
            _, cnt = np.unique(key, return_counts=True)
            max_pass = max(max_pass, int(cnt.max()))
        for h in np.flatnonzero(r.silence):
            q = np.flatnonzero(r.hypotheses.cell == h)
            if len(q) >= p.max_hypotheses_per_cell:
                hyp_cap_hits += 1
            full = (r.hypotheses.tracks[q] >= 0).all(axis=1)
            if full.any() and dist[h] > 1000:
                n_far_full += 1
            bgfull = [qq for qq in q[full] if any(str(ts.icao24[k]).startswith("bg") for k in r.hypotheses.tracks[qq])]
            if bgfull:
                n_bg_full += 1
            cid = int(spec.cell_ids(r.tile.i[h], r.tile.j[h]))
            if dist[h] <= spec.cell_half_diag_m + 1:
                true_id = cid
            hyps = [cs.Hypothesis(cid, tuple(int(k) for k in r.hypotheses.tracks[qq]),
                                  (float(r.hypotheses.dt_lo_s[qq]), float(r.hypotheses.dt_hi_s[qq]))) for qq in q]
            cells.append((cid, r.tile.xyz[h], hyps, float(r.silence_penalty[h]), dist[h]))
    out = dict(label=label, offset=n_off, silence=n_sil, far_full=n_far_full, bg_full=n_bg_full, true_survives=true_id is not None,
               max_pass_per_cell_event=max_pass, cap_hits=hyp_cap_hits)
    if do_score and cells:
        sp = score.ScoreParams.from_dict({**CFG["score"], "refine_in_cell": False})
        dist_by = {c[0]: c[4] for c in cells}
        ranked = score.rank_cells(events, [c[:4] for c in cells], ts, START, C, sp, (prior.dt_lo_s, prior.dt_hi_s), 100.0)
        rk = ([r.cell_id for r in ranked].index(true_id) + 1) if true_id is not None else None
        amb = score.ambiguity([r.best.cost for r in ranked], [r.xyz_est for r in ranked], sp,
                              [r.best.n_unmatched for r in ranked])
        top = ranked[0]
        out.update(rank1_tracks="/".join(str(ts.icao24[k]) if k >= 0 else "-" for k in top.best.assignment))
        out.update(true_rank=rk, n_ranked=len(ranked), rank1_err_m=round(float(dist_by[ranked[0].cell_id])),
                   rank1_nunmatched=ranked[0].best.n_unmatched,
                   true_nunmatched=next((r.best.n_unmatched for r in ranked if r.cell_id == true_id), None),
                   amb=amb["status"])
    return out


if __name__ == "__main__":
    Ks = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else [10, 30, 60, 120]
    seeds = range(int(sys.argv[2])) if len(sys.argv) > 2 else range(3)
    P_rob = params()
    P_str = params(**STRICT)
    for K in Ks:
        for s in seeds:
            t0 = time.time()
            trs, events, mic = scenario(K, 1000 * K + s)
            rows = [analyse(trs, events, P_str, "strict"), analyse(trs, events, P_rob, "robust")]
            no2 = [t for t in trs if t.icao24 != "ev1"]
            rows += [analyse(no2, events, P_str, "strict/noadsb2", do_score=False), analyse(no2, events, P_rob, "robust/noadsb2")]
            for r in rows:
                print(f"K={K:3d} seed={s} " + " ".join(f"{k}={v}" for k, v in r.items()), flush=True)
            print(f"   ({time.time() - t0:.0f}s)", flush=True)
