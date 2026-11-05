"""Time-shift null tests (doppler/nulltest.py): shift wrapping, comparison logic, and a real search."""
import numpy as np
import pytest

import cli
from doppler import aircraft_types as at
from doppler import cascade as cs
from doppler import nulltest as nt

CFG = cli.load_config("config.yaml")


def _event(k, t_rx, ridge_half=20.0, v=60.0, d=300.0):
    t = np.arange(t_rx - ridge_half, t_rx + ridge_half + 1e-9, 0.5)
    return cs.ObservedEvent(k, at.class_code("prop"), t_rx, v, d, 100.0, np.diag([0.25, 100.0, 0.0025]), (5.0, 5.0),
                            t, 100.0 + 0.01 * (t - t_rx), np.ones(len(t)))


def test_diagnostics_are_off_by_default():
    dp = nt.DiagnosticsParams.from_dict(CFG["diagnostics"])
    assert dp.null_test is False and dp.commit_result is False
    assert dp.null_shifts_frac == (0.25, 0.5, 0.75) and dp.commit_top_k == 5
    assert nt.DiagnosticsParams.from_dict(None) == nt.DiagnosticsParams()


def test_shift_seconds_are_fractions_of_the_clip():
    assert nt.shift_seconds((0.25, 0.5, 0.75), 600.0) == [150.0, 300.0, 450.0]
    for bad in (0.0, 1.0, -0.25, 1.5):
        with pytest.raises(ValueError):
            nt.shift_seconds((bad,), 600.0)


def test_wrap_time_reenters_at_the_start():
    assert nt.wrap_time(420.0, 150.0, 600.0) == 570.0
    assert nt.wrap_time(420.0, 300.0, 600.0) == pytest.approx(120.0)
    assert nt.wrap_time(150.0, 450.0, 600.0) == 0.0          # exactly the end wraps to the start
    assert 0.0 <= nt.wrap_time(599.999, 0.002, 600.0) < 600.0


def test_shift_events_moves_each_event_rigidly_and_wraps_it_as_a_unit():
    ev = [_event(0, 150.0), _event(1, 420.0)]
    out = nt.shift_events(ev, 300.0, 600.0)
    assert [e.t_rx_s for e in out] == pytest.approx([450.0, 120.0])
    for a, b in zip(ev, out):
        # the curve keeps its shape: ridge times move by the same amount as t_rx, frequencies are untouched
        assert b.ridge_t_s - b.t_rx_s == pytest.approx(a.ridge_t_s - a.t_rx_s)
        assert np.array_equal(a.ridge_f_hz, b.ridge_f_hz) and np.array_equal(a.ridge_w, b.ridge_w)
        assert (b.v_mps, b.d_m, b.f0_hz, b.idx, b.cls) == (a.v_mps, a.d_m, a.f0_hz, a.idx, a.cls)
        assert np.array_equal(a.cov, b.cov)
    assert ev[1].t_rx_s == 420.0 and ev[1].ridge_t_s[0] == 400.0       # inputs are not modified
    # the wrapped event's ridge stays whole: it reaches back past the clip start rather than being split
    assert out[1].ridge_t_s[0] == pytest.approx(100.0)
    near_end = nt.shift_events([_event(0, 590.0)], 15.0, 600.0)[0]
    assert near_end.t_rx_s == pytest.approx(5.0) and near_end.ridge_t_s[0] == pytest.approx(-15.0)
    assert nt.ridge_span_s([near_end]) == pytest.approx((-15.0, 25.0))


def test_four_quarter_shifts_return_every_event_to_its_place():
    ev = [_event(0, 37.5), _event(1, 512.25)]
    out = ev
    for _ in range(4):
        out = nt.shift_events(out, 150.0, 600.0)
    for a, b in zip(ev, out):
        assert b.t_rx_s == pytest.approx(a.t_rx_s) and b.ridge_t_s == pytest.approx(a.ridge_t_s)


def _best(n_explained, score, n_events=2, n_candidates=10):
    b = nt.SearchBest(n_events=n_events, n_candidates=n_candidates)
    if n_candidates:
        b.n_explained, b.score = n_explained, score
    return b


def test_summary_compares_explained_events_before_score():
    real = _best(2, 1.0)
    shifted = [(0.25, 150.0, _best(1, 0.2)), (0.5, 300.0, _best(2, 3.0)), (0.75, 450.0, _best(0, 0, n_candidates=0))]
    s = nt.summarize(real, shifted)
    assert s["best_shifted"] == 1                 # 2 explained at 3.0 beats 1 explained at 0.2
    assert s["explained_gap"] == 0 and s["score_gap"] == pytest.approx(2.0)
    assert s["best_shifted_over_real"] == pytest.approx(3.0)
    assert s["n_shifted_not_worse"] == 0
    assert [r["shift_s"] for r in s["shifted"]] == [150.0, 300.0, 450.0]
    assert s["shifted"][2]["n_explained"] is None and s["shifted"][2]["n_candidates"] == 0


def test_summary_reports_an_explained_gap_without_a_score_ratio():
    s = nt.summarize(_best(2, 1.0), [(0.5, 300.0, _best(1, 0.1))])
    assert s["explained_gap"] == 1 and s["score_gap"] is None and s["best_shifted_over_real"] is None
    assert s["n_shifted_not_worse"] == 0
    s = nt.summarize(_best(1, 1.0), [(0.5, 300.0, _best(2, 9.0)), (0.25, 150.0, _best(1, 0.5))])
    assert s["best_shifted"] == 0 and s["explained_gap"] == -1 and s["n_shifted_not_worse"] == 2


def test_summary_when_the_real_search_has_no_candidate():
    s = nt.summarize(_best(0, 0, n_candidates=0), [(0.5, 300.0, _best(1, 2.0))])
    assert s["explained_gap"] is None and s["n_shifted_not_worse"] == 1
    assert nt.summarize(_best(2, 1.0), [])["best_shifted"] is None
    # neither search found a candidate: no shift counts as "not worse"
    assert nt.summarize(_best(0, 0, n_candidates=0), [(0.5, 300.0, _best(0, 0, n_candidates=0))])["n_shifted_not_worse"] == 0


@pytest.mark.heavy                 # ~20 s locally (four cascade + scoring runs): after merge, not in the gate
def test_unshifted_search_reproduces_the_ranking_and_shifts_fit_worse():
    """search() at shift 0 gives the same rank 1 as scoring the cascade survivors directly (the locate path);
    on a clean two-event scenario every shifted timing ranks below the real one."""
    from doppler import geo, score, tracks

    from .conftest import START
    from .test_cascade import C, P, prepared_tracks, region_circle, ridge_events, scenario

    sc = scenario(0, 120.0, (30.0, 90.0))
    ev = ridge_events(sc)
    prep = prepared_tracks(sc)
    prior = cs.dt_prior_from("known", START, None, 10.0, 120.0)
    ts = tracks.build_trackset(prep, START - 120, START + 180, 0.25)
    spec = geo.make_grid_spec(region_circle(), 200.0, P.tile_cells)
    sp = score.ScoreParams.from_dict(CFG["score"])
    dem = geo.FlatDEM(1600.0)

    cells = []
    for r in cs.iter_cascade(spec, ev, ts, prior, dem, 1.5, C, P):
        for h in np.flatnonzero(r.silence):
            cid = int(spec.cell_ids(r.tile.i[h], r.tile.j[h]))
            hyps = [cs.Hypothesis(cid, tuple(int(k) for k in r.hypotheses.tracks[q]),
                                  (float(r.hypotheses.dt_lo_s[q]), float(r.hypotheses.dt_hi_s[q])))
                    for q in np.flatnonzero(r.hypotheses.cell == h)]
            cells.append((float(r.hypotheses.cost[r.best[h]]), (cid, r.tile.xyz[h], hyps, float(r.silence_penalty[h]))))
    cells.sort(key=lambda x: x[0])
    ref = score.rank_cells(ev, [c_ for _, c_ in cells], ts, START, C, sp, (prior.dt_lo_s, prior.dt_hi_s), 100.0)
    ref.sort(key=lambda r: (sum(k < 0 for k in r.best.assignment), r.best.cost))

    real = nt.search(spec, ev, ts, prior, dem, 1.5, C, P, sp)
    assert real.n_candidates == len(cells) and real.n_explained == 2
    assert real.score == ref[0].best.cost and real.dt_s == ref[0].best.dt_s
    assert real.tracks == [str(ts.icao24[k]) for k in ref[0].best.assignment]
    assert real.tracks == [t.icao24 for t in sc.truth]

    shifted = [(f, s, nt.search(spec, nt.shift_events(ev, s, 120.0), ts, prior, dem, 1.5, C, P, sp))
               for f, s in zip((0.25, 0.5, 0.75), nt.shift_seconds((0.25, 0.5, 0.75), 120.0))]
    summ = nt.summarize(real, shifted)
    assert summ["n_shifted_not_worse"] == 0, summ
