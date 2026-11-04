"""Milestone 4 cascade: tolerances, CPA refinement, shared-Δt offset filter (N events), silence filter,
tile streaming, and synthetic validation (true cell survives; decoy targets are removed by silence).

Fast tests build ObservedEvents from synthetic truth (no audio analysis); the audio
end-to-end runs are in test_locate.py (slow)."""
import numpy as np
import pytest
import shapely

import cli
from doppler import aircraft_types as at
from doppler import cascade as cs
from doppler import geo, synth, tracks

from .conftest import FRAME, START

CFG = cli.load_config("config.yaml")
P = cs.CascadeParams.from_dict({**CFG["cascade"], "tile_cells": 24})
STRICT = dict(max_unmatched_events=0, silence_audibility="hard")   # milestone-4 semantics
PH = cs.CascadeParams.from_dict({**CFG["cascade"], "tile_cells": 24, **STRICT})
C = 343.4


def truth_events(sc, sig=(0.5, 10.0, 0.05)):
    """ObservedEvents from synthetic truth with a small diagonal covariance (v, d, t_rx)."""
    out = []
    for k, (t, s) in enumerate(zip(sc.truth, sc.event_specs)):
        out.append(cs.ObservedEvent(k, at.class_code(s.cls), t.t_cpa_received_rel_s, t.v_mps, t.d_m, t.f0_hz,
                                    np.diag(np.square(sig)), (5.0, 5.0), np.zeros(0), np.zeros(0), np.zeros(0)))
    return out


def region_circle(r_m=6000.0):
    return cli.circle_region(FRAME.lat0, FRAME.lon0, r_m)


def prepared_tracks(sc):
    prep = tracks.prepare_tracks(sc.tracks, tracks.TrackParams.from_dict(CFG["tracks"]))
    cli.classify_tracks(CFG, prep, sc.registry)
    return prep


def run(sc, events, p=P, spacing=200.0, targets=()):
    prep = prepared_tracks(sc)
    prior = cs.dt_prior_from("known", START, None, 10.0, sc.params.duration_s)
    ts = tracks.build_trackset(prep, START - 120, START + sc.params.duration_s + 60, 0.25)
    spec = geo.make_grid_spec(region_circle(), spacing, p.tile_cells)
    counts = dict(grid=0, cpa=0, offset=0, silence=0)
    true_cell, target_cells = None, {}
    mic = sc.mic_ecef
    tgt_xyz = [FRAME.enu_to_ecef(np.array([x, y, sc.mic_enu[2]])) for x, y in targets]
    for r in cs.iter_cascade(spec, events, ts, prior, geo.FlatDEM(1600.0), 1.5, C, p, all_tiles=True):
        counts["grid"] += len(r.tile)
        counts["cpa"] += int(r.cpa.sum())
        counts["offset"] += int(r.offset.sum())
        counts["silence"] += int(r.silence.sum())
        if not len(r.tile):
            continue
        for key, xyz in [("true", mic)] + [(k, x) for k, x in enumerate(tgt_xyz)]:
            d = np.linalg.norm(r.tile.xyz - xyz, axis=1)
            h = int(np.argmin(d))
            if d[h] <= spec.cell_half_diag_m + 1:
                st = (bool(r.cpa[h]), bool(r.offset[h]), bool(r.silence[h]), int(r.n_unexplained[h]))
                if key == "true":
                    true_cell = st
                else:
                    target_cells[key] = st
    return counts, true_cell, target_cells


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
def test_tolerances_covariance_widening_and_sys_terms():
    ev = cs.ObservedEvent(0, at.PROP, 100.0, 70.0, 700.0, 120.0, np.diag([1.0, 100.0, 0.01]), (5.0, 5.0),
                          np.zeros(0), np.zeros(0), np.zeros(0))
    p = cs.CascadeParams(n_sigma=3.0, wind_sys_mps=0.0, height_sys_m=0.0, timing_sys_s=0.0)
    t = cs.event_tolerances(ev, p, C, 0.0)
    assert t.v_mps == pytest.approx(3.0) and t.d_core_m == pytest.approx(30.0) and t.t_rx_s == pytest.approx(0.3)
    clipped = cs.ObservedEvent(**{**ev.__dict__, "coverage_tau": (0.5, 5.0)})
    t2 = cs.event_tolerances(clipped, p, C, 0.0)
    assert t2.w_cov == p.truncated_tol_mult and t2.v_mps == pytest.approx(3.0 * p.truncated_tol_mult)
    # wind propagates into d through d = v·τ; sys terms add in quadrature; grid term adds linearly
    pw = cs.CascadeParams(n_sigma=3.0, wind_sys_mps=14.0, height_sys_m=0.0, timing_sys_s=0.0)
    tw = cs.event_tolerances(ev, pw, C, 141.0)
    assert tw.v_mps == pytest.approx(np.hypot(3.0, 14.0))
    assert tw.d_core_m == pytest.approx(np.hypot(30.0, 700.0 * 14.0 / 70.0))
    assert tw.d_grid_m == 141.0
    assert tw.t_rx_s == pytest.approx(np.hypot(0.3, 700 * 14 / C ** 2) + 141 / 70 + 141 / C)
    assert tw.d_for(np.array([False, True]), 150.0)[1] == pytest.approx(np.hypot(tw.d_core_m, 150.0) + 141.0)


def test_observed_event_propagates_t_rx_variance():
    class R:
        t_s = np.arange(3.0); f_hz = np.ones(3)
    class E:
        cls = at.PROP; t_cpa_received_s = 10.0; v_mps = 60.0; d_m = 343.4; f0_hz = 100.0
        cov = np.diag([1.0, 4.0, 343.4 ** 2, 1.0]); coverage = (3.0, 3.0); ridge = R; noise_hz = 0.5
    ev = cs.observed_events([E], C)[0]
    assert ev.sigma == pytest.approx([2.0, 343.4, np.sqrt(2.0)])


def test_dt_prior_modes():
    k = cs.dt_prior_from("known", 100.0, None, 10.0, 60.0)
    assert (k.dt_lo_s, k.dt_hi_s) == (-10.0, 10.0)
    w = cs.dt_prior_from("window", None, (100.0, 400.0), 5.0, 60.0)
    assert (w.start_s, w.dt_lo_s, w.dt_hi_s) == (100.0, -5.0, 305.0)
    r = cs.dt_prior_from("relative", None, None, 0.0, 60.0, (0.0, 1000.0))
    assert (r.dt_lo_s, r.dt_hi_s) == (0.0, 940.0)


def _pass_table(cells, events, tracks_, dtc, tol):
    n = len(cells)
    z = np.zeros(n)
    return cs.PassTable(np.array(cells), np.array(events, np.int16), np.array(tracks_, np.int32), z, z, z, z,
                        np.array(dtc, float), np.full(n, tol), z, z, np.zeros((n, 3)))


def test_offset_filter_is_shared_dt_interval_intersection_for_n_events():
    prior = cs.DtPrior(0.0, -10.0, 10.0, 60.0)
    icao = np.array(["a", "b", "c", "d", "e"], object)
    # cell 0: three events agree near Δt≈+2 → survives with one hypothesis
    # cell 1: events 0/1 agree, event 2 only at Δt=−8 → no shared Δt → rejected
    # cell 2: event 1 has two candidate passes; only one is consistent
    pt = _pass_table([0, 0, 0, 1, 1, 1, 2, 2, 2, 2], [0, 1, 2, 0, 1, 2, 0, 1, 1, 2],
                     [0, 1, 2, 0, 1, 2, 0, 1, 3, 2], [2.0, 2.5, 1.5, 2.0, 2.5, -8.0, 0.0, 9.0, 0.5, 0.3], 1.0)
    keep, hyp = cs.offset_filter(pt, 3, 3, icao, prior, cs.CascadeParams(max_unmatched_events=0))
    assert keep.tolist() == [True, False, True]
    h0 = hyp.take(np.flatnonzero(hyp.cell == 0))
    assert len(h0) == 1 and h0.tracks[0].tolist() == [0, 1, 2]
    assert h0.dt_lo_s[0] == pytest.approx(1.5) and h0.dt_hi_s[0] == pytest.approx(2.5)
    h2 = hyp.take(np.flatnonzero(hyp.cell == 2))
    assert h2.tracks.tolist() == [[0, 3, 2]]
    # N = 2 reduces to the pairwise offset test: |Δ(dtc)| ≤ tol1 + tol2
    pt2 = _pass_table([0, 0, 1, 1], [0, 1, 0, 1], [0, 1, 0, 1], [0.0, 1.9, 0.0, 2.1], 1.0)
    keep2, _ = cs.offset_filter(pt2, 2, 2, icao, prior, cs.CascadeParams(max_unmatched_events=0))
    assert keep2.tolist() == [True, False]
    # prior bounds Δt
    pt3 = _pass_table([0, 0], [0, 1], [0, 1], [11.5, 11.5], 1.0)
    assert not cs.offset_filter(pt3, 1, 2, icao, prior, cs.CascadeParams(max_unmatched_events=0))[0][0]


def test_offset_filter_forbids_shared_aircraft_unless_allowed():
    prior = cs.DtPrior(0.0, -10.0, 10.0, 60.0)
    icao = np.array(["a", "a", "b"], object)            # tracks 0 and 1 are segments of one aircraft
    pt = _pass_table([0, 0], [0, 1], [0, 1], [0.0, 0.0], 1.0)
    assert not cs.offset_filter(pt, 1, 2, icao, prior, cs.CascadeParams(max_unmatched_events=0))[0][0]
    assert cs.offset_filter(pt, 1, 2, icao, prior, cs.CascadeParams(max_unmatched_events=0, allow_shared_tracks=True))[0][0]


def test_offset_filter_allows_up_to_k_unmatched_events():
    prior = cs.DtPrior(0.0, -10.0, 10.0, 60.0)
    icao = np.array(["a", "b", "c"], object)
    # events 0/1 agree; event 2 has no consistent pass (a car, or an aircraft without ADS-B)
    pt = _pass_table([0, 0, 0], [0, 1, 2], [0, 1, 2], [2.0, 2.5, -8.0], 1.0)
    keep0, _ = cs.offset_filter(pt, 1, 3, icao, prior, cs.CascadeParams(max_unmatched_events=0))
    keep1, hyp = cs.offset_filter(pt, 1, 3, icao, prior, cs.CascadeParams(max_unmatched_events=1))
    assert not keep0[0] and keep1[0]
    assert [0, 1, -1] in hyp.tracks.tolist()
    # unmatched hypotheses carry a worse interim cost than fully matched ones
    pt_all = _pass_table([0, 0, 0], [0, 1, 2], [0, 1, 2], [2.0, 2.5, 2.2], 1.0)
    _, h = cs.offset_filter(pt_all, 1, 3, icao, prior, cs.CascadeParams(max_unmatched_events=1))
    full = h.cost[[t == [0, 1, 2] for t in h.tracks.tolist()]]
    part = h.cost[[-1 in t for t in h.tracks.tolist()]]
    assert full.min() < part.min()


def test_probabilistic_silence_penalises_soft_and_rejects_near_certain():
    from scipy.stats import norm

    p = cs.CascadeParams(silence_audibility="probabilistic", silence_level_sigma_db=6.0, silence_hard_prob=0.99)
    # P(audible) at r = thr/2 is Φ(6.02/6) ≈ 0.84 -> penalty only; at r = thr/5, Φ(2.33) ≈ 0.99 -> reject
    assert norm.cdf(20 * np.log10(2.0) / 6) < 0.99 < norm.cdf(20 * np.log10(5.2) / 6)


def test_cpa_refinement_finds_closest_point_between_samples():
    # straight track along x at 100 m/s sampled every 0.25 s; mic offset 500 m in y
    t = np.arange(0, 60, 0.25)
    lat, lon, h = FRAME.to_geodetic(np.c_[100 * (t - 30.1), np.zeros_like(t), np.full_like(t, 2000.0)])
    tr = tracks.Track("x", "X", START + t, lat, lon, h, np.tile([100.0, 0, 0], (len(t), 1)), cls=at.PROP)
    ts = tracks.build_trackset([tr], START, START + 60, 0.25)
    P = FRAME.enu_to_ecef(np.array([0.0, 500.0, 1600.0]))[None]
    s = np.array([int(np.nanargmin(np.linalg.norm(ts.xyz[0] - P, axis=1)))])
    t_emit, r, pos, vel = cs._refine_cpa(P, ts, np.array([0]), s)
    assert t_emit[0] - START == pytest.approx(30.1, abs=1e-3)
    assert r[0] == pytest.approx(np.hypot(500.0, 400.0), abs=0.5)  # ENU up vs ellipsoid curvature: < 1 m
    assert np.linalg.norm(vel[0]) == pytest.approx(100.0, rel=1e-6)


def test_silence_unknown_class_uses_lenient_threshold():
    assert CFG["cascade"]["audible_slant_m"]["unknown"] == min(CFG["cascade"]["audible_slant_m"][k]
                                                              for k in ("prop", "jet"))


def test_grid_tiles_cover_region_without_overlap():
    spec = geo.make_grid_spec(region_circle(3000.0), 200.0, 8)
    ids = []
    for i0, j0 in spec.tile_origins():
        tl = geo.build_tile(spec, int(i0), int(j0), geo.FlatDEM(1600.0), 1.5)
        ids.append(spec.cell_ids(tl.i, tl.j))
        assert np.allclose(geo.ecef_to_geodetic(tl.xyz)[2], 1601.5)
    ids = np.concatenate(ids)
    assert len(ids) == len(np.unique(ids))
    n, exact = geo.count_grid_cells(spec)
    assert exact and n == len(ids) == pytest.approx(np.pi * 3000 ** 2 / 200 ** 2, rel=0.03)


# ---------------------------------------------------------------------------
# Synthetic validation (truth-derived events; no audio analysis)
# ---------------------------------------------------------------------------
def scenario(seed, duration, times, classes=None, **kw):
    p = synth.SynthParams(seed=seed, duration_s=duration, event_rx_times_s=times, event_classes=classes,
                          audible_slant_m=CFG["cascade"]["audible_slant_m"], n_decoys_per_event=2,
                          n_twins_per_event=2, n_background=8, unknown_type_frac=0.1, **kw)
    return synth.make_scenario(p, FRAME, START, 1600.0, C)


@pytest.mark.parametrize("seed,duration,times,classes", [
    (0, 120.0, (30.0, 90.0), None),
    (1, 60.0, (15.0, 45.0), None),
    (2, 240.0, (60.0, 180.0), ("prop", "jet")),
    (3, 120.0, (60.0,), None),
    (4, 180.0, (30.0, 90.0, 150.0), None),
])
def test_true_cell_survives_every_stage(seed, duration, times, classes):
    sc = scenario(seed, duration, times, classes)
    counts, true_cell, _ = run(sc, truth_events(sc))
    assert true_cell is not None
    assert true_cell[:3] == (True, True, True), (counts, true_cell)
    assert counts["grid"] > counts["cpa"] >= counts["offset"] >= counts["silence"] >= 1


@pytest.mark.parametrize("seed", [0, 3, 5])
def test_cascade_decoys_are_removed_by_silence(seed):
    """Decoys over cells that survive CPA + offset: the silence filter must remove those cells, keep the true one."""
    sc0 = scenario(seed, 120.0, (60.0,))
    ev = truth_events(sc0)
    prep = prepared_tracks(sc0)
    prior = cs.dt_prior_from("known", START, None, 10.0, 120.0)
    ts = tracks.build_trackset(prep, START - 120, START + 180, 0.25)
    spec = geo.make_grid_spec(region_circle(), 200.0, P.tile_cells)
    cands = np.concatenate([FRAME.ecef_to_enu(r.tile.xyz[r.offset])[:, :2]
                            for r in cs.iter_cascade(spec, ev, ts, prior, geo.FlatDEM(1600.0), 1.5, C, PH)])
    targets = cli.choose_decoy_targets(cands, sc0.mic_enu, sc0.params, 3)
    assert targets, "single-event scenarios have survivors far from the mic"
    sc = synth.make_scenario(sc0.params, FRAME, START, 1600.0, C, mic_enu=sc0.mic_enu, decoy_targets_enu=targets)
    assert [t.t_cpa_received_rel_s for t in sc.truth] == pytest.approx([t.t_cpa_received_rel_s for t in sc0.truth])
    placed = [d for d in sc.decoys if d.icao24 and d.role == "decoy" and d.event_index is None]
    assert placed
    counts, true_cell, tcells = run(sc, truth_events(sc), p=PH, targets=[d.target_enu for d in placed])
    assert true_cell[:3] == (True, True, True)
    for k, st in tcells.items():
        assert st[1] and not st[2] and st[3] >= 1, (k, st)   # reached the silence stage, rejected there


def test_silence_count_mode_keeps_cells_but_counts():
    sc = scenario(3, 120.0, (60.0,))
    ev = truth_events(sc)
    a, _, _ = run(sc, ev, p=PH)
    b, _, _ = run(sc, ev, p=cs.CascadeParams.from_dict({**PH.__dict__, "silence_mode": "count"}))
    assert b["silence"] == b["offset"] == a["offset"] and a["silence"] <= a["offset"]


def test_tile_size_does_not_change_survivors():
    sc = scenario(0, 120.0, (30.0, 90.0))
    ev = truth_events(sc)
    a, _, _ = run(sc, ev, p=cs.CascadeParams.from_dict({**P.__dict__, "tile_cells": 7}))
    b, _, _ = run(sc, ev, p=cs.CascadeParams.from_dict({**P.__dict__, "tile_cells": 64}))
    assert a == b


# ---------------------------------------------------------------------------
# Milestone 5: locked scoring ranks the true cell first (truth-derived ridges, no audio analysis)
# ---------------------------------------------------------------------------
def ridge_events(sc, noise_hz=0.1, seed=0):
    """ObservedEvents whose ridges come from the general model at the true mic, plus noise and the truth f0."""
    from doppler import physics as ph

    rng = np.random.default_rng(seed)
    out = truth_events(sc)
    ev_enu = [e for e, s in zip(sc.track_enu, sc.specs) if s.role == "event"]
    t_track = sc.track_time_rel()
    res = []
    for e, enu, tru in zip(out, ev_enu, sc.truth):
        tr = np.arange(max(0.0, tru.t_cpa_received_rel_s - 60), min(sc.params.duration_s, tru.t_cpa_received_rel_s + 60), 0.1)
        ratio, _, _ = ph.predicted_ratio_at(tr, t_track, enu, sc.mic_enu, C)
        f = tru.f0_hz * ratio + rng.normal(0, noise_hz, len(tr))
        res.append(cs.ObservedEvent(**{**e.__dict__, "ridge_t_s": tr, "ridge_f_hz": f,
                                       "ridge_w": np.full(len(tr), 1 / noise_hz ** 2)}))
    return res


@pytest.mark.parametrize("seed,duration,times,classes", [
    (0, 120.0, (30.0, 90.0), None),
    (1, 60.0, (15.0, 45.0), None),
    (2, 240.0, (60.0, 180.0), ("prop", "jet")),
    (4, 180.0, (30.0, 90.0, 150.0), None),
])
def test_true_cell_ranks_first_after_locked_scoring(seed, duration, times, classes):
    from doppler import score

    sc = scenario(seed, duration, times, classes)
    ev = ridge_events(sc)
    prep = prepared_tracks(sc)
    prior = cs.dt_prior_from("known", START, None, 10.0, duration)
    ts = tracks.build_trackset(prep, START - 120, START + duration + 60, 0.25)
    spec = geo.make_grid_spec(region_circle(), 200.0, P.tile_cells)
    cells, true_id = [], None
    from pyproj import Transformer
    tx, ty = Transformer.from_crs("EPSG:4326", spec.crs, always_xy=True).transform(sc.mic_lon, sc.mic_lat)
    true_id = int(spec.cell_ids(int(np.floor(tx / 200.0)), int(np.floor(ty / 200.0))))
    for r in cs.iter_cascade(spec, ev, ts, prior, geo.FlatDEM(1600.0), 1.5, C, P):
        for h in np.flatnonzero(r.silence):
            cid = int(spec.cell_ids(r.tile.i[h], r.tile.j[h]))
            hyps = [cs.Hypothesis(cid, tuple(int(k) for k in r.hypotheses.tracks[q]),
                                  (float(r.hypotheses.dt_lo_s[q]), float(r.hypotheses.dt_hi_s[q])))
                    for q in np.flatnonzero(r.hypotheses.cell == h)]
            cells.append((cid, r.tile.xyz[h], hyps))
    assert any(cid == true_id for cid, _, _ in cells)
    sp = score.ScoreParams.from_dict(CFG["score"])
    ranked = score.rank_cells(ev, cells, ts, START, C, sp, (prior.dt_lo_s, prior.dt_hi_s), 100.0)
    assert ranked[0].cell_id == true_id, [(r.cell_id == true_id, round(r.best.cost, 3)) for r in ranked[:5]]
    assert np.linalg.norm(ranked[0].xyz_est - sc.mic_ecef) < 60.0
    assert abs(ranked[0].best.dt_s) < 0.3               # scenario clock is exact
    ev_icao = {t.icao24 for t in sc.truth}
    assert {str(ts.icao24[k]) for k in ranked[0].best.assignment} == ev_icao
