"""Two-stage refinement (score.refine_lbfgs_top_k, arm V1k): start grid for every refined cell, L-BFGS-B only for
the best K. With K unset (the default) rank_cells is unchanged; tests/test_golden_rows.py checks that end to end."""
import dataclasses

import numpy as np
import pytest

from doppler import cascade as cs
from doppler import score

from .test_score import START, _two_track_setup


@pytest.fixture(scope="module")
def setup():
    ts, mic, evs = _two_track_setup()
    B = score._cell_basis(mic)
    offsets = [(-70, 60), (130, -40), (-230, 160), (30, 250), (-330, -180), (260, 210)]
    cells = [(i, mic + e * B[0] + n * B[1], [cs.Hypothesis(i, (0, 1), (-5.0, 5.0))], 0.1 * i)
             for i, (e, n) in enumerate(offsets)]
    return ts, evs, cells


def _rank(setup, **kw):
    ts, evs, cells = setup
    p = dataclasses.replace(score.ScoreParams(), **kw)
    return score.rank_cells(evs, cells, ts, START, 343.0, p, (-10.0, 10.0), 100.0)


def _fields(r):
    b = r.best
    return (r.cell_id, r.refined, b.dt_s, b.cost, tuple(b.f0_hz), tuple(r.offset_en_m), tuple(r.xyz_est))


def test_k_at_least_the_refined_cells_equals_the_default(setup):
    base = [_fields(r) for r in _rank(setup)]
    assert [_fields(r) for r in _rank(setup, refine_lbfgs_top_k=6)] == base
    assert [_fields(r) for r in _rank(setup, refine_lbfgs_top_k=100)] == base


def test_k_zero_scores_each_cell_at_its_best_start_point(setup):
    ts, evs, cells = setup
    full = {r.cell_id: r for r in _rank(setup)}
    grid = {r.cell_id: r for r in _rank(setup, refine_lbfgs_top_k=0)}
    p = score.ScoreParams()
    ridges = score.prepare_ridges(evs, p)
    for cid, xyz, hyps, pen_raw in cells:
        g, f = grid[cid], full[cid]
        assert g.refined
        assert g.best.cost <= g.score_centre + 1e-15          # never worse than the centre
        assert g.best.cost >= f.best.cost - 1e-12             # L-BFGS-B never loses to its own start point
        init = score.score_hypothesis(hyps[0], ridges, ts, xyz, START, 343.0, p, (-10.0, 10.0))
        s1, _ = score.refine_start(hyps[0], ridges, ts, xyz, 100.0, START, 343.0, p, init, (-10.0, 10.0))
        pen = g.best.silence_term
        assert g.best.cost - pen == pytest.approx(min(s1, init.cost), rel=1e-12)


def test_only_the_best_k_by_start_grid_score_get_lbfgs(setup):
    full = {r.cell_id: _fields(r) for r in _rank(setup)}
    grid = {r.cell_id: _fields(r) for r in _rank(setup, refine_lbfgs_top_k=0)}
    two = {r.cell_id: _fields(r) for r in _rank(setup, refine_lbfgs_top_k=2)}
    same_as_full = {c for c in two if two[c] == full[c] and full[c] != grid[c]}
    same_as_grid = {c for c in two if two[c] == grid[c] and full[c] != grid[c]}
    assert len(same_as_full) == 2 and same_as_full | same_as_grid == {c for c in two if full[c] != grid[c]}
    # the cells that got L-BFGS-B are the best two by start-grid score + silence penalty
    ts, evs, cells = setup
    p = score.ScoreParams()
    ridges = score.prepare_ridges(evs, p)
    total = sum(len(r.t_s) for r in ridges)
    key = {}
    for cid, xyz, hyps, pen_raw in cells:
        init = score.score_hypothesis(hyps[0], ridges, ts, xyz, START, 343.0, p, (-10.0, 10.0))
        s1, _ = score.refine_start(hyps[0], ridges, ts, xyz, 100.0, START, 343.0, p, init, (-10.0, 10.0))
        key[cid] = s1 + pen_raw * p.silence_weight / total
    best2 = set(sorted(key, key=key.get)[:2])
    assert same_as_full == best2


def test_default_is_unset():
    assert score.ScoreParams().refine_lbfgs_top_k is None
