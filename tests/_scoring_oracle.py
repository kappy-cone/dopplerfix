"""Frozen one-at-a-time versions of locked-scoring internals, kept as test oracles.

Copied verbatim from main at f6baa69, before refinement was batched (score.refine_in_cell) and track sampling
packed (TrackSet._sample_one); ``score_hypothesis`` from main at 0f094d8, before centre scoring ran in lockstep
(score.score_cell_lockstep, ADR-0006). The batched versions must reproduce these bit for bit
(tests/test_score.py, tests/test_score_lockstep.py).
Never edit them to match a change: a deliberate result change replaces this oracle in its own commit.
"""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from doppler.cascade import Hypothesis
from doppler.score import CellScore, RidgeObs, ScoreParams, _cell_basis, _hyp_cost, _pooled
from doppler.tracks import TrackSet


def sample_one(self: TrackSet, k: int, t: NDArray) -> tuple[NDArray, NDArray]:
    """TrackSet._sample_one before packing (assign over TrackSet._sample_one to run the oracle path)."""
    u = (t - self.t0_s) / self.dt_s
    i0 = np.floor(u).astype(np.int64)
    T = self.xyz.shape[1]
    ok = (i0 >= 0) & (i0 + 1 < T)
    i0c = np.minimum(np.maximum(i0, 0), T - 2)
    w = (u - i0c)[..., None]
    X, V = self.xyz[k], self.vel[k]
    p = (1 - w) * X[i0c] + w * X[i0c + 1]
    v = (1 - w) * V[i0c] + w * V[i0c + 1]
    p[~ok] = np.nan
    v[~ok] = np.nan
    return p, v


def refine_in_cell(hyp: Hypothesis, ridges: list[RidgeObs], ts: TrackSet, xyz: NDArray, half_m: float,
                   start_time_s: float, c: float, p: ScoreParams, init: CellScore,
                   dt_prior: tuple[float, float] | None = None) -> tuple[CellScore, NDArray, NDArray]:
    """Best score within the cell's square footprint (±half_m east/north, height of the cell's DEM sample).

    Joint bounded optimisation of (east, north, Δt) — Δt still shared by every event, f0 profiled per event —
    started from the best of a 3×3 sub-grid. Returns (CellScore at the optimum, (east, north) offset in m,
    refined ECEF position).
    """
    from scipy.optimize import minimize

    B = _cell_basis(xyz)
    n_pts = np.array([len(r.t_s) for r in ridges])
    total = max(int(n_pts[[k_ >= 0 for k_ in hyp.assignment]].sum()), 1)
    lo, hi = init.dt_s - p.refine_dt_window_s, init.dt_s + p.refine_dt_window_s
    if dt_prior is not None:
        lo, hi = max(lo, dt_prior[0]), min(hi, dt_prior[1])

    def pos(u):
        return xyz + u[0] * B[0] + u[1] * B[1]

    def total_cost(u, dts):
        return _hyp_cost(np.atleast_1d(dts), ridges, hyp.assignment, pos(u), ts, start_time_s, c, p.huber_k,
                         p)[0].sum(0)

    starts = []
    dgrid = np.linspace(lo, hi, 13)
    for a in (-2 / 3, 0.0, 2 / 3):
        for b in (-2 / 3, 0.0, 2 / 3):
            u = np.array([a * half_m, b * half_m])
            cst = total_cost(u, dgrid)
            j = int(np.argmin(cst))
            starts.append((float(cst[j]), u, float(dgrid[j])))
    _, u0, d0 = min(starts, key=lambda z: z[0])
    scale = np.array([100.0, 100.0, 1.0])               # condition: 100 m ~ 1 s
    x0 = np.array([u0[0], u0[1], d0]) / scale
    bnds = [(-half_m / 100, half_m / 100), (-half_m / 100, half_m / 100), (lo, hi)]
    res = minimize(lambda z: float(total_cost(z[:2] * 100.0, z[2])[0]), x0, method="L-BFGS-B", bounds=bnds,
                   options={"eps": 1e-3, "maxiter": 60})
    z = res.x * scale if res.fun <= min(starts, key=lambda q: q[0])[0] else np.r_[u0, d0]
    u, dt = z[:2], float(z[2])
    cst, f0, rms, miss = _hyp_cost(np.array([dt]), ridges, hyp.assignment, pos(u), ts, start_time_s, c, p.huber_k,
                                   p)
    sc = CellScore(hyp.cell_id, tuple(hyp.assignment), dt, f0[:, 0], _pooled(cst[:, 0].sum(), total, p),
                   cst[:, 0] / np.maximum(n_pts, 1), n_pts, rms[:, 0], miss[:, 0], n_unmatched=init.n_unmatched)
    if sc.cost > init.cost:                              # never worse than the centre
        return init, np.zeros(2), xyz
    return sc, u, pos(u)




def score_hypothesis(hyp: Hypothesis, ridges: list[RidgeObs], ts: TrackSet, xyz: NDArray, start_time_s: float,
                     c: float, p: ScoreParams, dt_prior: tuple[float, float] | None = None) -> CellScore:
    """Locked scoring of one hypothesis: one Δt for every event (coarse grid + bounded refinement), one f0 each."""
    from scipy.optimize import minimize_scalar

    lo, hi = hyp.dt_bounds_s
    lo, hi = lo - p.dt_pad_s, hi + p.dt_pad_s
    if dt_prior is not None:
        lo, hi = max(lo, dt_prior[0]), min(hi, dt_prior[1])
    n_pts = np.array([len(r.t_s) for r in ridges])
    matched = np.array([k_ >= 0 for k_ in hyp.assignment])
    total = max(int(n_pts[matched].sum()), 1)
    grid = np.arange(lo, hi + 1e-9, p.dt_coarse_step_s) if hi > lo else np.array([lo])
    cost, *_ = _hyp_cost(grid, ridges, hyp.assignment, xyz, ts, start_time_s, c, p.huber_k, p)
    j = int(np.argmin(cost.sum(0)))
    a, b = max(lo, grid[j] - p.dt_coarse_step_s), min(hi, grid[j] + p.dt_coarse_step_s)
    best = grid[j]
    if b > a:
        res = minimize_scalar(lambda d: float(_hyp_cost(np.array([d]), ridges, hyp.assignment, xyz, ts,
                                                        start_time_s, c, p.huber_k, p)[0].sum()),
                              bounds=(a, b), method="bounded", options={"xatol": 1e-3})
        if res.fun <= cost.sum(0)[j]:
            best = float(res.x)
    cst, f0, rms, miss = _hyp_cost(np.array([best]), ridges, hyp.assignment, xyz, ts, start_time_s, c, p.huber_k, p)
    return CellScore(hyp.cell_id, tuple(hyp.assignment), float(best), f0[:, 0], _pooled(cst[:, 0].sum(), total, p),
                     cst[:, 0] / np.maximum(n_pts, 1), n_pts, rms[:, 0], miss[:, 0],
                     n_unmatched=int((~matched).sum()))


def score_cell(hyps, ridges, ts, xyz, start_time_s, c, p, dt_prior=None) -> list[CellScore]:
    """score.score_cell_lockstep before lockstep: one hypothesis at a time."""
    return [score_hypothesis(h, ridges, ts, xyz, start_time_s, c, p, dt_prior) for h in hyps]
