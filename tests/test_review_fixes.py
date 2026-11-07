"""Session 9 code-review fixes: vertical rate filled from altitude (default), silence per hypothesis and the
refinement order (arms, off by default; reports/apriori_registry.md "Code-review arms").

A new file rather than additions to test_tracks / test_cascade / test_score, so it doesn't collide with the
test work running in parallel (reports/test_plan.md T1/T2)."""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from doppler import cascade as cs
from doppler import geo, score, tracks
from doppler.tracks import Track

from .conftest import FRAME, START
from .test_cascade import CFG, PH, prepared_tracks, region_circle, scenario, truth_events
from .test_cascade import C as C_SOUND


# --- Fix 3: missing vertical rate --------------------------------------------------------------------------
def test_vertical_rate_filled_from_altitude_where_missing():
    t = np.arange(0.0, 60.0, 1.0)
    alt = 1000.0 + 10.0 * t                                   # climbing 10 m/s
    vz = np.full(t.size, 10.0)
    vz[[5, 20, 21, 40]] = np.nan
    out = tracks.vertical_rate_filled(t, alt, vz)
    assert np.all(np.isfinite(out)) and out == pytest.approx(np.full(t.size, 10.0))
    reported = np.full(t.size, 7.0)                           # reported values are kept as they are
    assert tracks.vertical_rate_filled(t, alt, reported) == pytest.approx(reported)
    assert tracks.vertical_rate_filled(t[:1], alt[:1], np.array([np.nan])) == pytest.approx([0.0])


def test_ingest_uses_altitude_rate_when_vertrate_is_missing():
    t = START + np.arange(0.0, 60.0, 1.0)
    enu = np.stack([np.zeros(t.size), 60.0 * (t - t[0]) - 1800.0, 500.0 + 10.0 * (t - t[0])], axis=1)
    lat, lon, h = FRAME.to_geodetic(enu)
    vr = np.full(t.size, 10.0)
    vr[::3] = np.nan                                          # a third of the fixes carry no vertical rate
    df = pd.DataFrame({"time": t, "icao24": "abc123", "callsign": "T1", "lat": lat, "lon": lon,
                       "geoaltitude": h, "velocity": 60.0, "heading": 0.0, "vertrate": vr})
    (tr,) = tracks.tracks_from_frame(df)
    assert tr.vel_enu is not None
    assert tr.vel_enu[:, 2] == pytest.approx(np.full(t.size, 10.0), abs=0.05), "not 0 where vertrate is missing"


# --- Fix 1: silence per hypothesis -------------------------------------------------------------------------
def _with_wingman(sc, prep, offset_e_m=150.0):
    """A second, class-compatible aircraft flying 150 m beside the event aircraft, as an unheard pass."""
    icao = sc.truth[0].icao24
    lead = [tr for tr in prep if tr.icao24 == icao]
    assert lead, "event aircraft not in the prepared tracks"
    out = list(prep)
    for k, tr in enumerate(lead):
        enu = tr.enu(FRAME) + np.array([offset_e_m, 0.0, 0.0])
        out.append(Track.from_enu(f"wing{k:02d}", "WING", tr.t_s, enu, FRAME, vel_enu=tr.vel_enu,
                                  alt_source=tr.alt_source, type_code=tr.type_code, cls=tr.cls))
    return out


def _true_cell_silence(sc, events, prep, p):
    prior = cs.dt_prior_from("known", START, None, 10.0, sc.params.duration_s)
    ts = tracks.build_trackset(prep, START - 120, START + sc.params.duration_s + 60, 0.25)
    spec = geo.make_grid_spec(region_circle(), 200.0, p.tile_cells)
    for r in cs.iter_cascade(spec, events, ts, prior, geo.FlatDEM(1600.0), 1.5, C_SOUND, p):
        if not len(r.tile):
            continue
        d = np.linalg.norm(r.tile.xyz - sc.mic_ecef, axis=1)
        h = int(np.argmin(d))
        if d[h] <= spec.cell_half_diag_m + 1:
            return bool(r.offset[h]), bool(r.silence[h]), r
    raise AssertionError("true cell not on the grid")


def test_silence_per_hypothesis_catches_an_unheard_wingman():
    """One heard flyover, two aircraft side by side. Cell scope excuses the wingman (it is CPA-consistent with
    the event); hypothesis scope does not, so with hard silence the true cell fails."""
    sc = scenario(1, 120.0, (60.0,))
    ev = truth_events(sc)
    prep = _with_wingman(sc, prepared_tracks(sc))
    cell_p = PH
    hyp_p = dataclasses.replace(PH, silence_scope="hypothesis")
    off_c, sil_c, _ = _true_cell_silence(sc, ev, prep, cell_p)
    off_h, sil_h, r = _true_cell_silence(sc, ev, prep, hyp_p)
    assert off_c and off_h, "both scopes see the same offset stage"
    assert sil_c, "cell scope: the wingman is explained by being CPA-consistent"
    assert not sil_h, "hypothesis scope: whichever aircraft is assigned, the other is an unheard audible pass"
    assert r.hyp_silence_keep is not None and len(r.hyp_silence_keep) == len(r.hypotheses)


def test_silence_per_hypothesis_keeps_the_true_cell_without_a_wingman():
    sc = scenario(1, 120.0, (60.0,))
    ev = truth_events(sc)
    prep = prepared_tracks(sc)
    off, sil, r = _true_cell_silence(sc, ev, prep, dataclasses.replace(PH, silence_scope="hypothesis"))
    assert off and sil
    h = int(np.argmin(np.linalg.norm(r.tile.xyz - sc.mic_ecef, axis=1)))
    qs, pen = r.scoring_hypotheses(h)
    assert len(qs) >= 1 and np.ndim(pen) == 1 and len(pen) == len(qs)
    assert r.best[h] in set(qs.tolist())


def test_cell_scope_is_the_default_and_unchanged():
    assert cs.CascadeParams().silence_scope == "cell" and CFG["cascade"]["silence_scope"] == "cell"
    with pytest.raises(ValueError, match="silence_scope"):
        sc = scenario(1, 120.0, (60.0,))
        _true_cell_silence(sc, truth_events(sc), prepared_tracks(sc), dataclasses.replace(PH, silence_scope="x"))


# --- Fix 2: refinement order -------------------------------------------------------------------------------
def test_refine_order_default_and_validation():
    assert score.ScoreParams().refine_order == "centre" and CFG["score"]["refine_order"] == "centre"
    with pytest.raises(ValueError, match="refine_order"):
        score.rank_cells([], [], None, 0.0, 343.0, score.ScoreParams(refine_order="x"), None, 100.0)


def test_per_hypothesis_penalty_picks_the_cell_best_with_its_penalty(monkeypatch):
    """rank_cells with one penalty per hypothesis chooses the cell's best on cost + penalty; with one penalty
    per cell (the default path) the choice is the cost alone, as before."""
    costs = {("a",): 1.0, ("b",): 1.2}

    def fake_score(h, ridges, ts, xyz, start, c, p, dt_prior):
        return score.CellScore(0, h, 0.0, np.zeros(1), costs[h], np.zeros(1), np.ones(1), np.zeros(1),
                               np.zeros(1))

    monkeypatch.setattr(score, "score_hypothesis", fake_score)
    monkeypatch.setattr(score, "prepare_ridges", lambda events, p: [score.RidgeObs(np.zeros(10), np.zeros(10), 1.0)])
    p = score.ScoreParams(refine_in_cell=False)
    hyps = [("a",), ("b",)]
    (r,) = score.rank_cells([], [(7, np.zeros(3), hyps, 0.0)], None, 0.0, 343.0, p, None, 100.0)
    assert r.best.assignment == ("a",)
    # per hypothesis: "a" carries a large silence penalty (nats; per point over 10 ridge points)
    (r,) = score.rank_cells([], [(7, np.zeros(3), hyps, np.array([5.0, 0.0]))], None, 0.0, 343.0, p, None, 100.0)
    assert r.best.assignment == ("b",) and r.best.silence_term == pytest.approx(0.0)
    assert r.best.cost == pytest.approx(1.2)


def test_refine_order_centre_silence_refines_by_the_ranked_score(monkeypatch):
    """With refine_max_cells = 1: cell 1 has the better centre score but a large penalty; cell 2 is best
    once the penalty counts. 'centre' refines cell 1, 'centre_silence' refines cell 2."""
    cost_by_cell = {1: 1.0, 2: 1.1}
    refined = []

    def fake_score(h, ridges, ts, xyz, start, c, p, dt_prior):
        return score.CellScore(int(xyz[0]), h, 0.0, np.zeros(1), cost_by_cell[int(xyz[0])], np.zeros(1),
                               np.ones(1), np.zeros(1), np.zeros(1))

    def fake_refine(hyp, ridges, ts, xyz, half_m, start, c, p, init, dt_prior=None):
        refined.append(int(xyz[0]))
        return init, np.zeros(2), xyz

    monkeypatch.setattr(score, "score_hypothesis", fake_score)
    monkeypatch.setattr(score, "refine_in_cell", fake_refine)
    monkeypatch.setattr(score, "prepare_ridges", lambda events, p: [score.RidgeObs(np.zeros(10), np.zeros(10), 1.0)])
    cells = [(1, np.array([1.0, 0, 0]), [("a",)], 5.0), (2, np.array([2.0, 0, 0]), [("b",)], 0.0)]
    for order, want in (("centre", 1), ("centre_silence", 2)):
        refined.clear()
        p = score.ScoreParams(refine_max_cells=1, refine_order=order)
        score.rank_cells([], cells, None, 0.0, 343.0, p, None, 100.0)
        assert refined == [want], order
