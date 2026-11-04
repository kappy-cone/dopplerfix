"""Full curve modeling (step 7) and locked N-event scoring (step 8).

Interface fixed in milestone 4:
  * :func:`modeled_ratio` — implemented: the general track-based model on the
    received-time axis (emission-time radial velocity, propagation delay).
  * :func:`score_hypotheses` — per hypothesis: one Δt shared by every event
    (coarse grid over the hypothesis' ``dt_bounds_s`` then bounded refinement)
    and one f0 per event, profiled out (closed-form L2 start, Huber IRLS) at each
    Δt. Cost = pooled mean Huber loss of ridge residuals in units of each
    event's ridge scatter; unmodelable ridge points get a fixed penalty.
    Relative event timing comes only from the physics; per-event t0s from the
    characterization fit are never used as scoring parameters.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from numpy.typing import NDArray

from .cascade import Hypothesis, ObservedEvent
from .tracks import TrackSet


@dataclass(frozen=True)
class ScoreParams:
    dt_coarse_step_s: float = 0.5          # coarse Δt grid step over each hypothesis' feasible interval
    dt_pad_s: float = 1.0                  # widen the feasible interval by this (still clipped to the prior)
    ridge_decimate_s: float = 0.5          # ridge points: one (median) per this much time
    huber_k: float = 3.0                   # Huber threshold in units of the event's ridge scatter σ
    sigma_floor_hz: float = 0.05           # lower bound on σ (a near-perfect synthetic ridge must not dominate)
    top_n: int = 10
    refine_in_cell: bool = True            # score a cell by its best point within its footprint (sub-cell search)
    refine_max_cells: int = 500            # refine at most this many cells (best by centre score first)
    refine_dt_window_s: float = 3.0        # Δt search half-width around the centre's Δt during refinement
    allow_shared_tracks: bool = False      # same aircraft assigned to two events (also used by the cascade)
    max_hypotheses_per_cell: int = 64

    @classmethod
    def from_dict(cls, d: dict) -> "ScoreParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CellScore:
    cell_id: int
    assignment: tuple[int, ...]
    dt_s: float
    f0_hz: NDArray            # (N,)
    cost: float               # pooled mean Huber loss over every event's ridge points (lower is better)
    per_event_cost: NDArray   # (N,)
    n_points: NDArray         # (N,)
    rms_hz: NDArray           # (N,) RMS residual of modeled points (Hz)
    n_missing: NDArray        # (N,) ridge points the track could not model


@dataclass(frozen=True)
class RidgeObs:
    """Decimated ridge of one event for scoring (recording-relative times)."""

    t_s: NDArray
    f_hz: NDArray
    sigma_hz: float


def prepare_ridges(events: Sequence[ObservedEvent], p: ScoreParams) -> list[RidgeObs]:
    """One median point per ``ridge_decimate_s`` bin; σ = the event's ridge scatter (1/√w), floored."""
    out = []
    for e in events:
        t, f = np.asarray(e.ridge_t_s, float), np.asarray(e.ridge_f_hz, float)
        ok = np.isfinite(t) & np.isfinite(f)
        t, f = t[ok], f[ok]
        if len(t) == 0:
            out.append(RidgeObs(t, f, 1.0))
            continue
        b = np.floor((t - t.min()) / p.ridge_decimate_s).astype(np.int64)
        ub, inv = np.unique(b, return_inverse=True)
        tt = np.array([np.median(t[inv == k]) for k in range(len(ub))])
        ff = np.array([np.median(f[inv == k]) for k in range(len(ub))])
        w = np.median(e.ridge_w) if len(e.ridge_w) else 1.0
        sig = max(float(1.0 / np.sqrt(w)) if w > 0 else 1.0, p.sigma_floor_hz)
        out.append(RidgeObs(tt, ff, sig))
    return out


def huber(z: NDArray, k: float) -> NDArray:
    a = np.abs(z)
    return np.where(a <= k, 0.5 * z ** 2, k * (a - 0.5 * k))


def _event_cost(f: NDArray, ratio: NDArray, sigma: float, k: float, n_iter: int = 3):
    """Profile f0 (Huber IRLS from the closed-form L2 start) along the last axis; returns (cost_sum, f0, rms, n_miss)."""
    ok = np.isfinite(ratio)
    R = np.where(ok, ratio, 0.0)
    F = np.broadcast_to(f, R.shape)
    w = ok.astype(float)
    f0 = np.sum(w * F * R, -1) / np.maximum(np.sum(w * R * R, -1), 1e-12)
    for _ in range(n_iter):
        r = F - f0[..., None] * R
        a = np.abs(r) / sigma
        w = np.where(ok, np.where(a <= k, 1.0, k / np.maximum(a, 1e-12)), 0.0)
        f0 = np.sum(w * F * R, -1) / np.maximum(np.sum(w * R * R, -1), 1e-12)
    r = np.where(ok, F - f0[..., None] * R, 0.0)
    miss = (~ok).sum(-1)
    cost = np.sum(np.where(ok, huber(r / sigma, k), 0.0), -1) + miss * huber(np.array(3.0 * k), k)
    n_ok = np.maximum(ok.sum(-1), 1)
    rms = np.sqrt(np.sum(r ** 2, -1) / n_ok)
    return cost, f0, rms, miss


def modeled_ratio(mic_xyz: NDArray, track: int, ts: TrackSet, t_rx_s: NDArray, c: float,
                  n_iter: int = 4) -> tuple[NDArray, NDArray, NDArray, NDArray]:
    """Observed/source frequency ratio at absolute received times for one track and mic (ECEF).

    Solves t_e + |A(t_e) − P|/c = t_r by Newton iteration (|dr/dt|/c < 1, so it
    converges fast), then ratio = c / (c + dr/dt_e). Returns
    ``(ratio, t_emit, range_m, valid)``; ``valid`` is False where the emission
    time falls outside the track's data.
    """
    P = np.asarray(mic_xyz, float).reshape(1, 3)
    tr = np.asarray(t_rx_s, float)
    k = np.full(tr.shape, int(track))
    A, _ = ts.sample(k, tr)
    te = tr - np.linalg.norm(A - P, axis=-1) / c
    for _ in range(n_iter):
        A, V = ts.sample(k, te)
        rel = A - P
        r = np.linalg.norm(rel, axis=-1)
        rdot = np.einsum("...k,...k->...", rel, V) / r
        g = te + r / c - tr
        te = te - np.where(np.isfinite(g), g / (1.0 + rdot / c), 0.0)
    A, V = ts.sample(k, te)
    rel = A - P
    r = np.linalg.norm(rel, axis=-1)
    rdot = np.einsum("...k,...k->...", rel, V) / r
    ratio = c / (c + rdot)
    return ratio, te, r, np.isfinite(ratio)


def profile_f0(f_obs: NDArray, ratio: NDArray, w: NDArray | None = None) -> float:
    """Closed-form least-squares f0 for f_obs ≈ f0·ratio."""
    w = np.ones_like(f_obs) if w is None else w
    m = np.isfinite(ratio) & np.isfinite(f_obs)
    den = np.sum(w[m] * ratio[m] ** 2)
    return float(np.sum(w[m] * f_obs[m] * ratio[m]) / den) if den > 0 else float("nan")


def _hyp_cost(dts: NDArray, ridges: list[RidgeObs], tracks: tuple[int, ...], xyz: NDArray, ts: TrackSet,
              start_s: float, c: float, k: float):
    """Pooled cost for each Δt in ``dts`` (G,), every event sharing that Δt. Returns per-event arrays (N, G)."""
    N, G = len(ridges), len(dts)
    cost = np.zeros((N, G))
    f0 = np.zeros((N, G))
    rms = np.zeros((N, G))
    miss = np.zeros((N, G), np.int64)
    for e, (rd, k_) in enumerate(zip(ridges, tracks)):
        if len(rd.t_s) == 0:
            continue
        t_rx = start_s + dts[:, None] + rd.t_s[None, :]
        ratio, _, _, _ = modeled_ratio(xyz, k_, ts, t_rx, c)
        cost[e], f0[e], rms[e], miss[e] = _event_cost(rd.f_hz, ratio, rd.sigma_hz, k)
    return cost, f0, rms, miss


def score_hypothesis(hyp: Hypothesis, ridges: list[RidgeObs], ts: TrackSet, xyz: NDArray, start_time_s: float,
                     c: float, p: ScoreParams, dt_prior: tuple[float, float] | None = None) -> CellScore:
    """Locked scoring of one hypothesis: one Δt for every event (coarse grid + bounded refinement), one f0 each."""
    from scipy.optimize import minimize_scalar

    lo, hi = hyp.dt_bounds_s
    lo, hi = lo - p.dt_pad_s, hi + p.dt_pad_s
    if dt_prior is not None:
        lo, hi = max(lo, dt_prior[0]), min(hi, dt_prior[1])
    n_pts = np.array([len(r.t_s) for r in ridges])
    total = max(int(n_pts.sum()), 1)
    grid = np.arange(lo, hi + 1e-9, p.dt_coarse_step_s) if hi > lo else np.array([lo])
    cost, *_ = _hyp_cost(grid, ridges, hyp.assignment, xyz, ts, start_time_s, c, p.huber_k)
    j = int(np.argmin(cost.sum(0)))
    a, b = max(lo, grid[j] - p.dt_coarse_step_s), min(hi, grid[j] + p.dt_coarse_step_s)
    best = grid[j]
    if b > a:
        res = minimize_scalar(lambda d: float(_hyp_cost(np.array([d]), ridges, hyp.assignment, xyz, ts,
                                                        start_time_s, c, p.huber_k)[0].sum()),
                              bounds=(a, b), method="bounded", options={"xatol": 1e-3})
        if res.fun <= cost.sum(0)[j]:
            best = float(res.x)
    cst, f0, rms, miss = _hyp_cost(np.array([best]), ridges, hyp.assignment, xyz, ts, start_time_s, c, p.huber_k)
    return CellScore(hyp.cell_id, tuple(hyp.assignment), float(best), f0[:, 0], float(cst[:, 0].sum() / total),
                     cst[:, 0] / np.maximum(n_pts, 1), n_pts, rms[:, 0], miss[:, 0])


def score_hypotheses(events: Sequence[ObservedEvent], hyps: Sequence[Hypothesis], ts: TrackSet,
                     cell_xyz: dict[int, NDArray], start_time_s: float, c: float,
                     p: ScoreParams, dt_prior: tuple[float, float] | None = None) -> list[CellScore]:
    """Locked N-event scoring of every hypothesis (one CellScore each, same order)."""
    ridges = prepare_ridges(events, p)
    return [score_hypothesis(h, ridges, ts, cell_xyz[h.cell_id], start_time_s, c, p, dt_prior) for h in hyps]


def best_per_cell(scores: Sequence[CellScore]) -> list[CellScore]:
    """Lowest-cost hypothesis per cell, sorted by cost (rank 1 first)."""
    best: dict[int, CellScore] = {}
    for sc in scores:
        if sc.cell_id not in best or sc.cost < best[sc.cell_id].cost:
            best[sc.cell_id] = sc
    return sorted(best.values(), key=lambda x: x.cost)


def _cell_basis(xyz: NDArray) -> NDArray:
    from .geo import ecef_to_geodetic, enu_basis

    lat, lon, _ = ecef_to_geodetic(xyz)
    return enu_basis(lat, lon)          # rows: east, north, up (ECEF)


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
    total = max(int(n_pts.sum()), 1)
    lo, hi = init.dt_s - p.refine_dt_window_s, init.dt_s + p.refine_dt_window_s
    if dt_prior is not None:
        lo, hi = max(lo, dt_prior[0]), min(hi, dt_prior[1])

    def pos(u):
        return xyz + u[0] * B[0] + u[1] * B[1]

    def total_cost(u, dts):
        return _hyp_cost(np.atleast_1d(dts), ridges, hyp.assignment, pos(u), ts, start_time_s, c, p.huber_k)[0].sum(0)

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
    cst, f0, rms, miss = _hyp_cost(np.array([dt]), ridges, hyp.assignment, pos(u), ts, start_time_s, c, p.huber_k)
    sc = CellScore(hyp.cell_id, tuple(hyp.assignment), dt, f0[:, 0], float(cst[:, 0].sum() / total),
                   cst[:, 0] / np.maximum(n_pts, 1), n_pts, rms[:, 0], miss[:, 0])
    if sc.cost > init.cost:                              # never worse than the centre
        return init, np.zeros(2), xyz
    return sc, u, pos(u)


def cpa_geometry(xyz: NDArray, track: int, ts: TrackSet, t_near_s: float | None = None,
                 search_s: float = 120.0) -> dict:
    """Closest approach of one track to a mic position (refined on the interpolated polyline).

    With ``t_near_s`` only samples within ±search_s of it are considered (a track can pass twice).
    """
    from .cascade import _refine_cpa

    d = np.linalg.norm(ts.xyz[track] - xyz, axis=1)
    if t_near_s is not None:
        d = np.where(np.abs(ts.t_s - t_near_s) <= search_s, d, np.nan)
    s0 = int(np.nanargmin(d))
    t_emit, r, pos, vel = _refine_cpa(np.asarray(xyz, float)[None], ts, np.array([track]), np.array([s0]))
    return {"t_emit_s": float(t_emit[0]), "r_m": float(r[0]), "speed_mps": float(np.linalg.norm(vel[0])),
            "pos": pos[0]}


@dataclass
class CellResult:
    """Ranked outcome for one cell: its best hypothesis, scored at the centre and (optionally) refined in-cell."""

    cell_id: int
    hyp_index: int                 # index into the cell's hypothesis list
    best: CellScore                # final (refined if refinement ran) score
    score_centre: float
    offset_en_m: NDArray           # (east, north) of the estimate from the cell centre
    xyz_est: NDArray
    refined: bool


def rank_cells(events: Sequence[ObservedEvent], cells: Sequence[tuple[int, NDArray, Sequence[Hypothesis]]],
               ts: TrackSet, start_time_s: float, c: float, p: ScoreParams,
               dt_prior: tuple[float, float] | None, half_cell_m: float) -> list[CellResult]:
    """Score every hypothesis of every cell (centre), keep each cell's best, refine the best
    ``refine_max_cells`` cells within their footprint, and return cells sorted by final score."""
    ridges = prepare_ridges(events, p)
    out = []
    for cell_id, xyz, hyps in cells:
        scs = [score_hypothesis(h, ridges, ts, xyz, start_time_s, c, p, dt_prior) for h in hyps]
        j = int(np.argmin([s_.cost for s_ in scs]))
        out.append(CellResult(cell_id, j, scs[j], scs[j].cost, np.zeros(2), np.asarray(xyz, float), False))
    if p.refine_in_cell:
        hyps_by = {cid: hyps for cid, _, hyps in cells}
        for r in sorted(out, key=lambda r: r.score_centre)[:p.refine_max_cells]:
            sc, off, xyz_r = refine_in_cell(hyps_by[r.cell_id][r.hyp_index], ridges, ts, r.xyz_est, half_cell_m,
                                            start_time_s, c, p, r.best, dt_prior)
            r.best, r.offset_en_m, r.xyz_est, r.refined = sc, off, xyz_r, True
    return sorted(out, key=lambda r: r.best.cost)
