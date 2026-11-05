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
    silence_weight: float = 1.0            # probabilistic-silence penalty (nats) added to the pooled loss
    ambiguity_rel: float = 0.15            # PROVISIONAL: candidates within this fraction of the best score are "tied"
    ambiguity_radius_m: float = 400.0      # tied candidates all within this of rank 1 -> "unique", else "ambiguous"
    inconsistent_rms_sigma: float = 3.0    # PROVISIONAL (P0.4; 3σ convention): rank-1 event rms above this × the
                                           # model-free ridge scatter -> status "inconsistent" (diagnostic)
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
    cost: float               # pooled mean Huber loss over the matched events' ridge points (lower is better)
    per_event_cost: NDArray   # (N,)
    n_points: NDArray         # (N,)
    rms_hz: NDArray           # (N,) RMS residual of modeled points (Hz); NaN for an unmatched event
    n_missing: NDArray        # (N,) ridge points the track could not model
    silence_term: float = 0.0 # probabilistic-silence penalty included in ``cost`` (per point)
    n_unmatched: int = 0      # events this hypothesis leaves without a track

    @property
    def rank_key(self) -> tuple:
        """Explain as many events as possible first (lexicographic), then the lowest cost."""
        return (self.n_unmatched, self.cost)


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
    ``(ratio, t_emit, range_m, valid)``; ``valid`` is False where the converged
    emission time falls outside the track's data.

    The iteration samples the track at times clamped into its coverage, so a
    received time past the segment end (whose emission time is r/c earlier and
    inside the data) still converges; only the final sample is unclamped.
    """
    # Hot path (≈ 75 % of V1 run time, profiled in session 5): one scalar track index, clamping with
    # maximum/minimum and the norm as sqrt(Σ x²), each bit-identical to np.clip / np.linalg.norm here but
    # without their per-call overhead on these small arrays.
    P = np.asarray(mic_xyz, float).reshape(1, 3)
    tr = np.asarray(t_rx_s, float)
    k = int(track)
    lo, hi = ts.coverage(track)
    if not np.isfinite(lo):
        nan = np.full(tr.shape, np.nan)
        return nan, nan, nan, np.zeros(tr.shape, bool)
    hi_c = max(lo, hi - 1e-6)

    def norm(x):
        return np.sqrt(np.add.reduce(x * x, axis=-1))

    A, _ = ts.sample(k, np.minimum(np.maximum(tr, lo), hi_c))
    te = tr - norm(A - P) / c
    for _ in range(n_iter):
        A, V = ts.sample(k, np.minimum(np.maximum(te, lo), hi_c))
        rel = A - P
        r = norm(rel)
        rdot = np.einsum("...k,...k->...", rel, V) / r
        g = te + r / c - tr
        te = te - np.where(np.isfinite(g), g / (1.0 + rdot / c), 0.0)
    A, V = ts.sample(k, te)
    rel = A - P
    r = norm(rel)
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
    """Pooled cost for each Δt in ``dts`` (G,), every event sharing that Δt. Returns per-event arrays (N, G).

    An event with track −1 (unmatched) contributes nothing here; hypotheses are ranked lexicographically
    by (number of unmatched events, cost), see CellScore.rank_key.
    """
    N, G = len(ridges), len(dts)
    cost = np.zeros((N, G))
    f0 = np.zeros((N, G))
    rms = np.zeros((N, G))
    miss = np.zeros((N, G), np.int64)
    for e, (rd, k_) in enumerate(zip(ridges, tracks)):
        if len(rd.t_s) == 0:
            continue
        if k_ < 0:
            f0[e], rms[e] = np.nan, np.nan
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
    matched = np.array([k_ >= 0 for k_ in hyp.assignment])
    total = max(int(n_pts[matched].sum()), 1)
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
                     cst[:, 0] / np.maximum(n_pts, 1), n_pts, rms[:, 0], miss[:, 0],
                     n_unmatched=int((~matched).sum()))


def score_hypotheses(events: Sequence[ObservedEvent], hyps: Sequence[Hypothesis], ts: TrackSet,
                     cell_xyz: dict[int, NDArray], start_time_s: float, c: float,
                     p: ScoreParams, dt_prior: tuple[float, float] | None = None) -> list[CellScore]:
    """Locked N-event scoring of every hypothesis (one CellScore each, same order)."""
    ridges = prepare_ridges(events, p)
    return [score_hypothesis(h, ridges, ts, cell_xyz[h.cell_id], start_time_s, c, p, dt_prior) for h in hyps]


def best_per_cell(scores: Sequence[CellScore]) -> list[CellScore]:
    """Best hypothesis per cell by rank_key, sorted (rank 1 first)."""
    best: dict[int, CellScore] = {}
    for sc in scores:
        if sc.cell_id not in best or sc.rank_key < best[sc.cell_id].rank_key:
            best[sc.cell_id] = sc
    return sorted(best.values(), key=lambda x: x.rank_key)


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
    total = max(int(n_pts[[k_ >= 0 for k_ in hyp.assignment]].sum()), 1)
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
                   cst[:, 0] / np.maximum(n_pts, 1), n_pts, rms[:, 0], miss[:, 0], n_unmatched=init.n_unmatched)
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


def rank_cells(events: Sequence[ObservedEvent], cells: Sequence[tuple], ts: TrackSet, start_time_s: float, c: float,
               p: ScoreParams, dt_prior: tuple[float, float] | None, half_cell_m: float) -> list[CellResult]:
    """Score every hypothesis of every cell (centre), keep each cell's best, refine the best
    ``refine_max_cells`` cells within their footprint, and return cells sorted by final score.

    ``cells``: (cell_id, xyz, hypotheses[, silence_penalty_nats]). The penalty (probabilistic silence) is
    added to the pooled loss as silence_weight · nats / total ridge points.
    """
    ridges = prepare_ridges(events, p)
    total = max(int(sum(len(r.t_s) for r in ridges)), 1)
    pen = {cl[0]: (float(cl[3]) if len(cl) > 3 else 0.0) * p.silence_weight / total for cl in cells}
    out = []
    for cell_id, xyz, hyps, *_ in cells:
        scs = [score_hypothesis(h, ridges, ts, xyz, start_time_s, c, p, dt_prior) for h in hyps]
        j = min(range(len(scs)), key=lambda i: scs[i].rank_key)
        out.append(CellResult(cell_id, j, scs[j], scs[j].cost, np.zeros(2), np.asarray(xyz, float), False))
    if p.refine_in_cell:
        hyps_by = {cl[0]: cl[2] for cl in cells}
        for r in sorted(out, key=lambda r: (r.best.n_unmatched, r.score_centre))[:p.refine_max_cells]:
            sc, off, xyz_r = refine_in_cell(hyps_by[r.cell_id][r.hyp_index], ridges, ts, r.xyz_est, half_cell_m,
                                            start_time_s, c, p, r.best, dt_prior)
            r.best, r.offset_en_m, r.xyz_est, r.refined = sc, off, xyz_r, True
    for r in out:
        r.best.silence_term = pen[r.cell_id]
        r.best.cost += pen[r.cell_id]
        r.score_centre += pen[r.cell_id]
    return sorted(out, key=lambda r: r.best.rank_key)


def ambiguity(scores: Sequence[float], xyz: Sequence[NDArray], p: ScoreParams,
              n_unmatched: Sequence[int] | None = None) -> dict:
    """How decisive the ranking is. ``scores``/``xyz`` sorted best first.

    The tied set is every candidate with score ≤ best·(1 + ambiguity_rel). The result is "unique" when the
    whole tied set lies within ambiguity_radius_m of rank 1, otherwise "ambiguous" (e.g. a single event,
    whose locus of equally good cells is long). Returns status, tied-set size and indices, spread, and the
    rank-2 / rank-1 score ratio.
    """
    sc = np.asarray(scores, float)
    if len(sc) == 0:
        return {"status": "none", "n_tied": 0, "tied": [], "spread_m": None, "gap_ratio": None}
    nu = np.zeros(len(sc), int) if n_unmatched is None else np.asarray(n_unmatched, int)
    tied = np.flatnonzero((sc <= sc[0] * (1.0 + p.ambiguity_rel)) & (nu == nu[0]))
    X = np.asarray([xyz[k] for k in tied], float)
    spread = float(np.max(np.linalg.norm(X - X[0], axis=1))) if len(X) else 0.0
    return {"status": "unique" if spread <= p.ambiguity_radius_m else "ambiguous", "n_tied": int(len(tied)),
            "tied": tied.tolist(), "spread_m": spread,
            "gap_ratio": float(sc[1] / sc[0]) if len(sc) > 1 and sc[0] > 0 else None}


def ridge_scatter_ref(t_s: NDArray, f_hz: NDArray, decimate_s: float = 0.5) -> float:
    """Model-free ridge scatter σ_ref (Hz) for the absolute-fit check (P0.4).

    Second differences of the decimated ridge remove the slowly varying Doppler curve. With AR(1) ridge noise
    (lag-1 correlation ρ) var(Δ²f) = σ²(6 − 8ρ + 2ρ²) and the lag-1 autocorrelation of Δ²f is
    (−4 + 7ρ − 4ρ² + ρ³)/(6 − 8ρ + 2ρ²); ρ is solved from the observed value and σ follows. Robust (MAD) scale.
    """
    t, f = np.asarray(t_s, float), np.asarray(f_hz, float)
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    if len(t) < 8:
        return float("nan")
    b = np.floor((t - t.min()) / decimate_s).astype(np.int64)
    ub, inv = np.unique(b, return_inverse=True)
    fd = np.array([np.median(f[inv == k]) for k in range(len(ub))])
    d2 = np.diff(fd, 2)
    if len(d2) < 6:
        return float("nan")
    mad = 1.4826 * np.median(np.abs(d2 - np.median(d2)))
    x = d2 - d2.mean()
    r1_obs = float(np.sum(x[1:] * x[:-1]) / max(np.sum(x * x), 1e-12))
    rho = np.linspace(0.0, 0.95, 96)
    den = 6 - 8 * rho + 2 * rho ** 2
    r1 = (-4 + 7 * rho - 4 * rho ** 2 + rho ** 3) / den
    k = int(np.argmin(np.abs(r1 - r1_obs)))
    return float(mad / np.sqrt(den[k]))


def absolute_fit_status(rms_hz: Sequence[float], sigma_ref_hz: Sequence[float], p: ScoreParams) -> dict:
    """Provisional absolute-fit check on the rank-1 hypothesis: "inconsistent" if any matched event's residual
    rms exceeds ``inconsistent_rms_sigma`` × its model-free ridge scatter. A status, not a ranking rule (D-4:
    under realism correct and wrong answers overlap in Hz-level misfit). Unmatched events (rms NaN) are skipped."""
    rms = np.asarray(rms_hz, float)
    ref = np.asarray(sigma_ref_hz, float)
    ratio = np.where(np.isfinite(rms) & np.isfinite(ref) & (ref > 0), rms / np.maximum(ref, 1e-12), np.nan)
    bad = bool(np.nanmax(ratio) > p.inconsistent_rms_sigma) if np.isfinite(ratio).any() else False
    return {"inconsistent": bad, "rms_over_ref": [None if not np.isfinite(r) else float(r) for r in ratio],
            "sigma_ref_hz": [None if not np.isfinite(r) else float(r) for r in ref]}
