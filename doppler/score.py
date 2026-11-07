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

from .cascade import Hypothesis, ObservedEvent, refine_cpa
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
    refine_lbfgs_top_k: int | None = None  # two-stage refinement (arm): every refined cell gets the 3×3 × 13 start
                                           # grid; only the best K by start-grid score (+ silence penalty) also get
                                           # L-BFGS-B. None: every refined cell does (the default)
    silence_weight: float = 1.0            # probabilistic-silence penalty (nats) added to the pooled loss
    ambiguity_rel: float = 0.15            # PROVISIONAL: candidates within this fraction of the best score are "tied"
    ambiguity_radius_m: float = 400.0      # tied candidates all within this of rank 1 -> "unique", else "ambiguous"
    inconsistent_rms_sigma: float = 3.0    # PROVISIONAL (P0.4; 3σ convention): rank-1 event rms above this × the
                                           # model-free ridge scatter -> status "inconsistent" (diagnostic)
    allow_shared_tracks: bool = False      # same aircraft assigned to two events (also used by the cascade)
    max_hypotheses_per_cell: int = 64
    # P1.1 calibrated likelihood (a comparison arm; "huber" keeps the step-8 pooled Huber loss). Values: registry.
    likelihood: str = "huber"              # huber | calibrated
    outlier_frac: float = 0.05             # ε: per-point outlier mixture weight (uniform over the ridge band)
    adsb_pos_m: float = 7.0                # ADS-B budget: position (DO-260B NACp class)
    adsb_speed_mps: float = 0.26           # ADS-B budget: speed (1 kt quantization / 2)
    adsb_latency_s: float = 0.5            # ADS-B budget: timestamp latency
    sigma_inflation_cap: float = 3.0       # σ_e from the hypothesis' own residuals, within [σ_white, cap·σ_white]
    # P1.4 ambiguity v2 (with the calibrated likelihood: Δnll in nats)
    ambiguity_mode: str = "v1"             # v1 (relative score) | v2 (Δnll tie set, assignment gap, gof, locus)
    tie_quantile: float = 0.95             # tied if 2·Δnll ≤ χ²₂ quantile
    gof_quantile: float = 0.99             # "inconsistent" if an event's χ² exceeds this χ²(N_eff) quantile

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
    # calibrated likelihood (P1.1) only; unused by the Huber loss
    sigma_white_hz: float = float("nan")   # model-free scatter of the decimated ridge (floored at sigma_floor_hz)
    kappa: float = 1.0                     # effective-N factor (1 + ρ₁)/(1 − ρ₁), hypothesis-independent
    band_hz: float = float("nan")          # width of the frequency band the ridge was searched in
    v_mps: float = float("nan")            # the event's fitted speed (position budget -> time-equivalent error)


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
        extra = {}
        if p.likelihood == "calibrated":
            sw, rho = ridge_white_stats(t, f, p.ridge_decimate_s)
            band = getattr(e, "band_hz", None)
            width = (float(band[1] - band[0]) if band is not None and band[1] > band[0]
                     else float(ff.max() - ff.min()) + 10.0 * sig)     # no band recorded (tests): ridge span + 10σ
            extra = {"sigma_white_hz": max(sw, p.sigma_floor_hz) if np.isfinite(sw) else sig,
                     "kappa": (1.0 + rho) / (1.0 - rho) if np.isfinite(rho) else 1.0,
                     "band_hz": max(width, 10.0 * sig), "v_mps": float(e.v_mps)}
        out.append(RidgeObs(tt, ff, sig, **extra))
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

    ``mic_xyz`` is one position (3,), or one per row (M, 3) with ``t_rx_s`` (M, n): refinement evaluates several
    nearby positions in one call. Each row's arithmetic is the same as a single-position call.

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
    P = np.asarray(mic_xyz, float)
    P = P.reshape(1, 3) if P.ndim == 1 else P.reshape(-1, 1, 3)
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


def _ridge_sigma2(rd: RidgeObs, model: NDArray, ratio: NDArray, c: float, p: ScoreParams) -> tuple[NDArray, NDArray]:
    """(σ_white², σ_model²) per point: the model-free ridge scatter and the ADS-B budget mapped to frequency.

    The budget: a ground-speed error δv changes the radial velocity by δv·cos θ (cos θ = ṙ/v, ṙ = c(1/R − 1)
    from the ratio R), so the frequency by f0·R²·δv·|cos θ|/c; a timestamp latency, and the along-track part of a
    position error (as δp / v), shift the curve in time by δt, i.e. by ḟ·δt. Cross-track position error is
    neglected (it changes only the curve's width, at second order).
    """
    v = max(rd.v_mps if np.isfinite(rd.v_mps) else 50.0, 1.0)
    fdot = np.gradient(np.where(np.isfinite(model), model, 0.0), rd.t_s, axis=-1) if model.shape[-1] > 1 \
        else np.zeros_like(model)
    with np.errstate(divide="ignore", invalid="ignore"):
        cos_t = np.clip(np.abs(c * (1.0 / ratio - 1.0)) / v, 0.0, 1.0)
    t_err2 = p.adsb_latency_s ** 2 + (p.adsb_pos_m / v) ** 2
    s_model2 = (model * ratio * p.adsb_speed_mps * cos_t / c) ** 2 + fdot ** 2 * t_err2
    return np.full(model.shape, rd.sigma_white_hz ** 2), np.where(np.isfinite(s_model2), s_model2, 0.0)


def _event_nll(rd: RidgeObs, ratio: NDArray, c: float, p: ScoreParams):
    """P1.1 calibrated per-event negative log-likelihood (nats) along the last axis; same returns as _event_cost.

    Per point: −ln(ε/B + (1 − ε)·N(r; 0, s²)) with s² = σ_e² + σ_model², a proper density in Hz (B = ridge band
    width, so both mixture terms are densities); an unmodellable point costs −ln(ε/B). σ_e is the hypothesis' own
    residual RMS, kept within [σ_white, cap·σ_white]. The event's sum is divided by the hypothesis-independent
    effective-N factor κ. f0 is profiled as in the Huber loss (IRLS at σ_white).
    """
    f = rd.f_hz
    ok = np.isfinite(ratio)
    R = np.where(ok, ratio, 0.0)
    F = np.broadcast_to(f, R.shape)
    w = ok.astype(float)
    sw = rd.sigma_white_hz
    f0 = np.sum(w * F * R, -1) / np.maximum(np.sum(w * R * R, -1), 1e-12)
    for _ in range(3):
        a = np.abs(F - f0[..., None] * R) / sw
        w = np.where(ok, np.where(a <= p.huber_k, 1.0, p.huber_k / np.maximum(a, 1e-12)), 0.0)
        f0 = np.sum(w * F * R, -1) / np.maximum(np.sum(w * R * R, -1), 1e-12)
    model = np.where(ok, f0[..., None] * R, np.nan)
    r = np.where(ok, F - f0[..., None] * R, 0.0)
    n_ok = np.maximum(ok.sum(-1), 1)
    rms = np.sqrt(np.sum(r ** 2, -1) / n_ok)
    # robust (MAD) scale, so an outlier point costs only its own term instead of widening every point's density
    if ok.all():
        mad = 1.4826 * np.median(np.abs(r), -1)
    else:
        import warnings

        with warnings.catch_warnings():                    # a Δt with no modellable point: scale -> σ_white
            warnings.simplefilter("ignore", RuntimeWarning)
            mad = 1.4826 * np.nan_to_num(np.nanmedian(np.where(ok, np.abs(r), np.nan), -1), nan=0.0)
    sig_e = np.clip(mad, sw, p.sigma_inflation_cap * sw)
    _, s_model2 = _ridge_sigma2(rd, model, np.where(ok, ratio, np.nan), c, p)
    s2 = sig_e[..., None] ** 2 + np.where(ok, s_model2, 0.0)
    eps, B = p.outlier_frac, rd.band_hz
    dens = eps / B + (1.0 - eps) * np.exp(-0.5 * r ** 2 / s2) / np.sqrt(2.0 * np.pi * s2)
    point = np.where(ok, -np.log(dens), -np.log(eps / B))
    miss = (~ok).sum(-1)
    return point.sum(-1) / rd.kappa, f0, rms, miss


def _unmatched_nll(rd: RidgeObs, p: ScoreParams) -> float:
    """An event left without a track: every point is "unmodellable", −ln(ε/B) each (κ = 1)."""
    return float(len(rd.t_s) * -np.log(p.outlier_frac / rd.band_hz))


def _hyp_cost(dts: NDArray, ridges: list[RidgeObs], tracks: tuple[int, ...], xyz: NDArray, ts: TrackSet,
              start_s: float, c: float, k: float, p: ScoreParams | None = None):
    """Pooled cost for each Δt in ``dts`` (G,), every event sharing that Δt. Returns per-event arrays (N, G).

    ``xyz`` is the mic position (3,), or one position per Δt (G, 3), so a batch of (position, Δt) points costs one
    call (``refine_in_cell``); column g is then bit-identical to a single-position call at (xyz[g], dts[g]).

    An event with track −1 (unmatched) contributes nothing here under the Huber loss; hypotheses are ranked
    lexicographically by (number of unmatched events, cost), see CellScore.rank_key. With the calibrated
    likelihood (``p.likelihood == "calibrated"``) costs are nats and an unmatched event costs its points as
    unmodellable.
    """
    cal = p is not None and p.likelihood == "calibrated"
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
            if cal:
                cost[e] = _unmatched_nll(rd, p)
            continue
        t_rx = start_s + dts[:, None] + rd.t_s[None, :]
        ratio, _, _, _ = modeled_ratio(xyz, k_, ts, t_rx, c)
        if cal:
            cost[e], f0[e], rms[e], miss[e] = _event_nll(rd, ratio, c, p)
        else:
            cost[e], f0[e], rms[e], miss[e] = _event_cost(rd.f_hz, ratio, rd.sigma_hz, k)
    return cost, f0, rms, miss


def _pooled(cost_sum: float, total_points: int, p: ScoreParams) -> float:
    """Hypothesis cost from the summed per-event cost: the Huber loss is a mean per matched point, the
    calibrated likelihood a total in nats."""
    return float(cost_sum) if p.likelihood == "calibrated" else float(cost_sum / total_points)


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


def score_hypotheses(events: Sequence[ObservedEvent], hyps: Sequence[Hypothesis], ts: TrackSet,
                     cell_xyz: dict[int, NDArray], start_time_s: float, c: float,
                     p: ScoreParams, dt_prior: tuple[float, float] | None = None) -> list[CellScore]:
    """Locked N-event scoring of every hypothesis (one CellScore each, same order)."""
    ridges = prepare_ridges(events, p)
    return [score_hypothesis(h, ridges, ts, cell_xyz[h.cell_id], start_time_s, c, p, dt_prior) for h in hyps]


def _bounded_brent(x1: float, x2: float, xatol: float = 1e-5, maxiter: int = 500):
    """scipy 1.18.1 ``_minimize_scalar_bounded`` as a generator (ADR-0006): yields each probe x, receives f(x).

    Ported line for line (the same scalar float arithmetic, display code dropped), so the probe sequence and the
    result are those of ``minimize_scalar(method="bounded")``; tests/test_score_lockstep.py checks it against
    scipy. Returns ``(x, fun)`` as StopIteration's value, with the types ``minimize_scalar`` gives them.
    """
    maxfun = maxiter
    sqrt_eps = np.sqrt(2.2e-16)
    golden_mean = 0.5 * (3.0 - np.sqrt(5.0))
    a, b = x1, x2
    fulc = a + golden_mean * (b - a)
    nfc, xf = fulc, fulc
    rat = e = 0.0
    x = xf
    fx = yield x
    num = 1
    fu = np.inf

    ffulc = fnfc = fx
    xm = 0.5 * (a + b)
    tol1 = sqrt_eps * np.abs(xf) + xatol / 3.0
    tol2 = 2.0 * tol1

    while (np.abs(xf - xm) > (tol2 - 0.5 * (b - a))):
        golden = 1
        # Check for parabolic fit
        if np.abs(e) > tol1:
            golden = 0
            r = (xf - nfc) * (fx - ffulc)
            q = (xf - fulc) * (fx - fnfc)
            p = (xf - fulc) * q - (xf - nfc) * r
            q = 2.0 * (q - r)
            if q > 0.0:
                p = -p
            q = np.abs(q)
            r = e
            e = rat

            # Check for acceptability of parabola
            if ((np.abs(p) < np.abs(0.5 * q * r)) and (p > q * (a - xf)) and
                    (p < q * (b - xf))):
                rat = (p + 0.0) / q
                x = xf + rat

                if ((x - a) < tol2) or ((b - x) < tol2):
                    si = np.sign(xm - xf) + ((xm - xf) == 0)
                    rat = tol1 * si
            else:      # do a golden-section step
                golden = 1

        if golden:  # do a golden-section step
            if xf >= xm:
                e = a - xf
            else:
                e = b - xf
            rat = golden_mean * e

        si = np.sign(rat) + (rat == 0)
        x = xf + si * np.maximum(np.abs(rat), tol1)
        fu = yield x
        num += 1

        if fu <= fx:
            if x >= xf:
                a = xf
            else:
                b = xf
            fulc, ffulc = nfc, fnfc
            nfc, fnfc = xf, fx
            xf, fx = x, fu
        else:
            if x < xf:
                a = x
            else:
                b = x
            if (fu <= fnfc) or (nfc == xf):
                fulc, ffulc = nfc, fnfc
                nfc, fnfc = x, fu
            elif (fu <= ffulc) or (fulc == xf) or (fulc == nfc):
                fulc, ffulc = x, fu

        xm = 0.5 * (a + b)
        tol1 = sqrt_eps * np.abs(xf) + xatol / 3.0
        tol2 = 2.0 * tol1

        if num >= maxfun:
            break

    fun = np.asarray(fx)[()]                      # as minimize_scalar reshapes res.fun / res.x
    return np.reshape(xf, fun.shape)[()], fun


def _lockstep_costs(dts: dict[int, NDArray], hyps: Sequence[Hypothesis], ridges: list[RidgeObs], xyz: NDArray,
                    ts: TrackSet, start_s: float, c: float, k: float) -> dict[int, tuple]:
    """``_hyp_cost`` (Huber loss) for several hypotheses of one cell at once: ``dts[i]`` are hypothesis i's Δt values.

    Each event's term depends only on (cell, the event's track, Δt), so the requests are grouped by
    (event, track) and each group costs one ``modeled_ratio`` / ``_event_cost`` call, its Δt values stacked as
    rows. Row results are bit-identical to a call with those rows alone (tests/test_score.py checks the
    modeled_ratio rows), so each hypothesis' (cost, f0, rms, miss) arrays (N, G_i) equal ``_hyp_cost``'s.
    """
    N = len(ridges)
    out, groups = {}, {}
    for i, d in dts.items():
        G = len(d)
        cost, f0, rms, miss = np.zeros((N, G)), np.zeros((N, G)), np.zeros((N, G)), np.zeros((N, G), np.int64)
        for e, (rd, k_) in enumerate(zip(ridges, hyps[i].assignment)):
            if len(rd.t_s) == 0:
                continue
            if k_ < 0:
                f0[e], rms[e] = np.nan, np.nan
                continue
            groups.setdefault((e, int(k_)), []).append(i)
        out[i] = (cost, f0, rms, miss)
    for (e, k_), idx in groups.items():
        rd = ridges[e]
        d = np.concatenate([dts[i] for i in idx]) if len(idx) > 1 else dts[idx[0]]
        t_rx = start_s + d[:, None] + rd.t_s[None, :]
        ratio, _, _, _ = modeled_ratio(xyz, k_, ts, t_rx, c)
        cst, f0, rms, miss = _event_cost(rd.f_hz, ratio, rd.sigma_hz, k)
        o = 0
        for i in idx:
            G = len(dts[i])
            r = out[i]
            r[0][e], r[1][e], r[2][e], r[3][e] = cst[o:o + G], f0[o:o + G], rms[o:o + G], miss[o:o + G]
            o += G
    return out


def score_cell_lockstep(hyps: Sequence[Hypothesis], ridges: list[RidgeObs], ts: TrackSet, xyz: NDArray,
                        start_time_s: float, c: float, p: ScoreParams,
                        dt_prior: tuple[float, float] | None = None) -> list[CellScore]:
    """``score_hypothesis`` for every hypothesis of one cell, in lockstep (ADR-0006); identical results, same order.

    The three phases of ``score_hypothesis`` (coarse Δt grid, bounded Brent, final evaluation) each run for all
    hypotheses together: every Brent search is a generator (:func:`_bounded_brent`) advanced one probe at a time,
    and each round's probes cost one call per (event, track) (:func:`_lockstep_costs`) instead of one per
    (hypothesis, event). Sums over events use the same numpy expressions as ``score_hypothesis``, on arrays of
    the same shape. The calibrated likelihood (a comparison arm) keeps the one-at-a-time path: its robust scale
    branches on the whole batch (``_event_nll``), so stacking hypotheses could change a result.
    """
    if p.likelihood == "calibrated" or len(hyps) == 0:
        return [score_hypothesis(h, ridges, ts, xyz, start_time_s, c, p, dt_prior) for h in hyps]
    H = len(hyps)
    n_pts = np.array([len(r.t_s) for r in ridges])
    setup, grids = [], {}
    for i, hyp in enumerate(hyps):
        lo, hi = hyp.dt_bounds_s
        lo, hi = lo - p.dt_pad_s, hi + p.dt_pad_s
        if dt_prior is not None:
            lo, hi = max(lo, dt_prior[0]), min(hi, dt_prior[1])
        matched = np.array([k_ >= 0 for k_ in hyp.assignment])
        total = max(int(n_pts[matched].sum()), 1)
        grids[i] = np.arange(lo, hi + 1e-9, p.dt_coarse_step_s) if hi > lo else np.array([lo])
        setup.append((lo, hi, matched, total))
    coarse = _lockstep_costs(grids, hyps, ridges, xyz, ts, start_time_s, c, p.huber_k)
    best, gens, probe, gsum = [None] * H, {}, {}, [None] * H
    for i in range(H):
        lo, hi = setup[i][:2]
        grid = grids[i]
        gsum[i] = coarse[i][0].sum(0)
        j = int(np.argmin(gsum[i]))
        a, b = max(lo, grid[j] - p.dt_coarse_step_s), min(hi, grid[j] + p.dt_coarse_step_s)
        best[i] = (grid[j], j)
        if b > a:
            gens[i] = _bounded_brent(a, b, xatol=1e-3)
            probe[i] = next(gens[i])
    while probe:                                        # one Brent probe per active hypothesis per round
        res = _lockstep_costs({i: np.array([x]) for i, x in probe.items()}, hyps, ridges, xyz, ts, start_time_s, c,
                              p.huber_k)
        nxt = {}
        for i in probe:
            try:
                nxt[i] = gens[i].send(float(res[i][0].sum()))
            except StopIteration as stop:
                x, fun = stop.value
                g, j = best[i]
                if fun <= gsum[i][j]:
                    best[i] = (float(x), j)
        probe = nxt
    final = _lockstep_costs({i: np.array([best[i][0]]) for i in range(H)}, hyps, ridges, xyz, ts, start_time_s, c,
                            p.huber_k)
    out = []
    for i, hyp in enumerate(hyps):
        cst, f0, rms, miss = final[i]
        matched, total = setup[i][2], setup[i][3]
        out.append(CellScore(hyp.cell_id, tuple(hyp.assignment), float(best[i][0]), f0[:, 0],
                             _pooled(cst[:, 0].sum(), total, p), cst[:, 0] / np.maximum(n_pts, 1), n_pts, rms[:, 0],
                             miss[:, 0], n_unmatched=int((~matched).sum())))
    return out


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


def _fd_points(z: NDArray, eps: float, lb: NDArray, ub: NDArray) -> list[NDArray]:
    """The forward-difference points scipy's L-BFGS-B evaluates for its gradient at ``z`` (``options.eps``).

    A prediction, so the cost at these points can be computed in the same call as the cost at ``z``. It mirrors
    ``scipy.optimize._numdiff.approx_derivative`` ('2-point', ``abs_step``, bounds). A wrong prediction costs only
    speed: scipy still asks for its own points, and any not computed here are computed when it asks.
    """
    try:
        from scipy.optimize._numdiff import _adjust_scheme_to_bounds
    except ImportError:                                   # a scipy without the helper: no prediction
        return []
    h = np.full(z.shape, float(eps))
    if np.any(((z + h) - z) == 0):                        # scipy falls back to a relative step there
        return []
    h, _ = _adjust_scheme_to_bounds(z, h, 1, "1-sided", lb, ub)
    pts = []
    for i in range(z.size):
        x1 = np.copy(z)
        x1[i] = z[i] + h[i]
        pts.append(x1)
    return pts


def refine_in_cell(hyp: Hypothesis, ridges: list[RidgeObs], ts: TrackSet, xyz: NDArray, half_m: float,
                   start_time_s: float, c: float, p: ScoreParams, init: CellScore,
                   dt_prior: tuple[float, float] | None = None, *, lbfgs: bool = True,
                   start: tuple | None = None) -> tuple[CellScore, NDArray, NDArray]:
    """Best score within the cell's square footprint (±half_m east/north, height of the cell's DEM sample).

    Joint bounded optimisation of (east, north, Δt) — Δt still shared by every event, f0 profiled per event —
    started from the best of a 3×3 sub-grid. Returns (CellScore at the optimum, (east, north) offset in m,
    refined ECEF position).

    Cost evaluations are batched without changing any result: the 3×3 × 13 start grid is one call, and each
    point L-BFGS-B visits is costed together with the three finite-difference points its gradient needs next
    (``_fd_points``), which scipy then collects through its ``workers`` hook. The sequence of points, and so
    every result, is the same as evaluating them one at a time (tests/test_score.py checks this against a
    frozen copy of the one-at-a-time version).

    Two-stage refinement (``ScoreParams.refine_lbfgs_top_k``): ``lbfgs=False`` stops after the start grid and
    scores its best point; ``start`` passes in a start grid already computed by ``refine_start`` (same values).
    """
    from scipy.optimize import minimize

    B, n_pts, total, lo, hi, pos, costs = _refine_setup(hyp, ridges, ts, xyz, start_time_s, c, p, init, dt_prior)
    starts = start if start is not None else _start_grid(costs, lo, hi, half_m)
    _, u0, d0 = min(starts, key=lambda z: z[0])
    if not lbfgs:
        return _refined_score(hyp, ridges, ts, pos, np.asarray(u0, float), float(d0), start_time_s, c, p, init,
                              n_pts, total, xyz)
    scale = np.array([100.0, 100.0, 1.0])               # condition: 100 m ~ 1 s
    x0 = np.array([u0[0], u0[1], d0]) / scale
    bnds = [(-half_m / 100, half_m / 100), (-half_m / 100, half_m / 100), (lo, hi)]
    eps = 1e-3
    lb, ub = np.array([b[0] for b in bnds]), np.array([b[1] for b in bnds])
    known: dict[bytes, float] = {}                      # cost at finite-difference points computed ahead

    def fun(zz):
        zz = np.asarray(zz, float)
        pts = [zz] + _fd_points(zz, eps, lb, ub)
        v = costs([x[:2] * 100.0 for x in pts], [x[2] for x in pts])
        for x, t in zip(pts[1:], v[1:]):
            known[x.tobytes()] = float(t)
        return float(v[0])

    def workers(_f, xs):
        xs = list(xs)
        todo = [x for x in xs if x.tobytes() not in known]
        if todo:
            for x, t in zip(todo, costs([x[:2] * 100.0 for x in todo], [x[2] for x in todo])):
                known[x.tobytes()] = float(t)
        return [np.atleast_1d(known[x.tobytes()]) for x in xs]

    res = minimize(fun, x0, method="L-BFGS-B", bounds=bnds, options={"eps": eps, "maxiter": 60, "workers": workers})
    z = res.x * scale if res.fun <= min(starts, key=lambda q: q[0])[0] else np.r_[u0, d0]
    return _refined_score(hyp, ridges, ts, pos, z[:2], float(z[2]), start_time_s, c, p, init, n_pts, total, xyz)


def _refine_setup(hyp, ridges, ts, xyz, start_time_s, c, p, init, dt_prior):
    """Shared pieces of refine_in_cell / refine_start: cell basis, point counts, Δt window, position and cost."""
    B = _cell_basis(xyz)
    n_pts = np.array([len(r.t_s) for r in ridges])
    total = max(int(n_pts[[k_ >= 0 for k_ in hyp.assignment]].sum()), 1)
    lo, hi = init.dt_s - p.refine_dt_window_s, init.dt_s + p.refine_dt_window_s
    if dt_prior is not None:
        lo, hi = max(lo, dt_prior[0]), min(hi, dt_prior[1])

    def pos(u):
        return xyz + u[0] * B[0] + u[1] * B[1]

    def costs(us, dts) -> NDArray:
        """Total cost at each (u, Δt) pair, one call."""
        xyzs = np.stack([pos(u) for u in us])
        return _hyp_cost(np.asarray(dts, float), ridges, hyp.assignment, xyzs, ts, start_time_s, c, p.huber_k,
                         p)[0].sum(0)

    return B, n_pts, total, lo, hi, pos, costs


def _start_grid(costs, lo: float, hi: float, half_m: float) -> list[tuple[float, NDArray, float]]:
    """The 3×3 × 13 start grid in one call: per sub-cell offset, (best total cost, offset, its Δt)."""
    dgrid = np.linspace(lo, hi, 13)
    grid_u = [np.array([a * half_m, b * half_m]) for a in (-2 / 3, 0.0, 2 / 3) for b in (-2 / 3, 0.0, 2 / 3)]
    grid_cost = costs([u for u in grid_u for _ in dgrid], np.tile(dgrid, len(grid_u))).reshape(len(grid_u), -1)
    starts = []
    for u, cst in zip(grid_u, grid_cost):
        j = int(np.argmin(cst))
        starts.append((float(cst[j]), u, float(dgrid[j])))
    return starts


def refine_start(hyp: Hypothesis, ridges: list[RidgeObs], ts: TrackSet, xyz: NDArray, half_m: float,
                 start_time_s: float, c: float, p: ScoreParams, init: CellScore,
                 dt_prior: tuple[float, float] | None = None) -> tuple[float, list]:
    """Stage 1 of two-stage refinement: the start grid of ``refine_in_cell`` and its best pooled score.

    Returns (pooled cost of the best start point, the start grid to hand to ``refine_in_cell(start=...)``).
    """
    _, _, total, lo, hi, _, costs = _refine_setup(hyp, ridges, ts, xyz, start_time_s, c, p, init, dt_prior)
    starts = _start_grid(costs, lo, hi, half_m)
    return _pooled(min(z[0] for z in starts), total, p), starts


def _refined_score(hyp, ridges, ts, pos, u, dt, start_time_s, c, p, init, n_pts, total, xyz):
    """CellScore at the refined point (u, Δt); the centre's when that is better (never worse than the centre)."""
    cst, f0, rms, miss = _hyp_cost(np.array([dt]), ridges, hyp.assignment, pos(u), ts, start_time_s, c, p.huber_k,
                                   p)
    sc = CellScore(hyp.cell_id, tuple(hyp.assignment), dt, f0[:, 0], _pooled(cst[:, 0].sum(), total, p),
                   cst[:, 0] / np.maximum(n_pts, 1), n_pts, rms[:, 0], miss[:, 0], n_unmatched=init.n_unmatched)
    if sc.cost > init.cost:                              # never worse than the centre
        return init, np.zeros(2), xyz
    return sc, u, pos(u)


def cpa_geometry(xyz: NDArray, track: int, ts: TrackSet, t_near_s: float | None = None,
                 search_s: float = 120.0) -> dict:
    """Closest approach of one track to a mic position (refined on the interpolated polyline).

    With ``t_near_s`` only samples within ±search_s of it are considered (a track can pass twice).
    """
    d = np.linalg.norm(ts.xyz[track] - xyz, axis=1)
    if t_near_s is not None:
        d = np.where(np.abs(ts.t_s - t_near_s) <= search_s, d, np.nan)
    s0 = int(np.nanargmin(d))
    t_emit, r, pos, vel = refine_cpa(np.asarray(xyz, float)[None], ts, np.array([track]), np.array([s0]))
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
    """Score every hypothesis of every cell (centre; in lockstep per cell, score_cell_lockstep), keep each cell's
    best, refine the best ``refine_max_cells`` cells within their footprint, and return cells sorted by final
    score.

    ``cells``: (cell_id, xyz, hypotheses[, silence_penalty_nats]). The penalty (probabilistic silence) is
    added to the pooled loss as silence_weight · nats / total ridge points.
    """
    ridges = prepare_ridges(events, p)
    total = max(int(sum(len(r.t_s) for r in ridges)), 1)
    # probabilistic-silence penalty: per point under the Huber loss (a mean), in nats under the calibrated one
    per = 1.0 if p.likelihood == "calibrated" else total
    pen = {cl[0]: (float(cl[3]) if len(cl) > 3 else 0.0) * p.silence_weight / per for cl in cells}
    out = []
    for cell_id, xyz, hyps, *_ in cells:
        scs = score_cell_lockstep(hyps, ridges, ts, xyz, start_time_s, c, p, dt_prior)
        j = min(range(len(scs)), key=lambda i: scs[i].rank_key)
        out.append(CellResult(cell_id, j, scs[j], scs[j].cost, np.zeros(2), np.asarray(xyz, float), False))
    if p.refine_in_cell:
        hyps_by = {cl[0]: cl[2] for cl in cells}
        chosen = sorted(out, key=lambda r: (r.best.n_unmatched, r.score_centre))[:p.refine_max_cells]
        K = p.refine_lbfgs_top_k
        if K is None:
            for r in chosen:
                sc, off, xyz_r = refine_in_cell(hyps_by[r.cell_id][r.hyp_index], ridges, ts, r.xyz_est, half_cell_m,
                                                start_time_s, c, p, r.best, dt_prior)
                r.best, r.offset_en_m, r.xyz_est, r.refined = sc, off, xyz_r, True
        else:
            # Two-stage: the start grid for every chosen cell; L-BFGS-B only for the best K of them by the score
            # cells are ranked on (start-grid pooled cost + silence penalty, unmatched count first).
            stage1 = [refine_start(hyps_by[r.cell_id][r.hyp_index], ridges, ts, r.xyz_est, half_cell_m, start_time_s,
                                   c, p, r.best, dt_prior) for r in chosen]
            order = sorted(range(len(chosen)), key=lambda i: (chosen[i].best.n_unmatched,
                                                             stage1[i][0] + pen[chosen[i].cell_id]))
            full = set(order[:max(int(K), 0)])
            for i, r in enumerate(chosen):
                sc, off, xyz_r = refine_in_cell(hyps_by[r.cell_id][r.hyp_index], ridges, ts, r.xyz_est, half_cell_m,
                                                start_time_s, c, p, r.best, dt_prior, lbfgs=i in full,
                                                start=stage1[i][1])
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


def ambiguity_v2(nll: Sequence[float], xyz: Sequence[NDArray], n_unmatched: Sequence[int],
                 assignments: Sequence[tuple], n_matched_rank1: int, p: ScoreParams) -> dict:
    """P1.4 ambiguity on the calibrated likelihood. Candidates sorted best first (lexicographic rank).

    * ``none``: no candidate.
    * ``locus``: rank 1 matches a single event (one flyover constrains only a locus).
    * tie set: candidates with rank 1's unmatched count and nll ≤ nll₁ + χ²₂(tie_quantile)/2.
    * ``unique`` if the tie set lies within ambiguity_radius_m of rank 1 and holds no other assignment
      (the assignment gap: the best different assignment is outside the tie set); else ``ambiguous``.
    """
    from scipy.stats import chi2

    sc = np.asarray(nll, float)
    if len(sc) == 0:
        return {"status": "none", "n_tied": 0, "tied": [], "spread_m": None, "gap_ratio": None,
                "dnll_tie": None, "assignment_gap_nll": None}
    nu = np.asarray(n_unmatched, int)
    dnll = float(chi2.ppf(p.tie_quantile, 2) / 2.0)
    same = nu == nu[0]
    tied = np.flatnonzero(same & (sc <= sc[0] + dnll))
    X = np.asarray([xyz[k] for k in tied], float)
    spread = float(np.max(np.linalg.norm(X - X[0], axis=1))) if len(X) else 0.0
    other = [k for k in np.flatnonzero(same) if tuple(assignments[k]) != tuple(assignments[0])]
    gap = float(sc[other[0]] - sc[0]) if other else None
    other_tied = any(tuple(assignments[k]) != tuple(assignments[0]) for k in tied)
    if n_matched_rank1 <= 1:
        status = "locus"
    else:
        status = "unique" if spread <= p.ambiguity_radius_m and not other_tied else "ambiguous"
    return {"status": status, "n_tied": int(len(tied)), "tied": tied.tolist(), "spread_m": spread,
            "gap_ratio": None, "dnll_tie": dnll, "assignment_gap_nll": gap}


def gof_status(ridges: list[RidgeObs], ts: TrackSet, xyz: NDArray, assignment: Sequence[int], dt_s: float,
               start_time_s: float, c: float, p: ScoreParams) -> dict:
    """P1.4 absolute fit with the P1.1 σ: per matched event, T = Σ r²/(σ_white² + σ_model²)/κ against χ²(n/κ).

    The scale here is the model-free one (not the hypothesis' own inflated σ_e), so a systematic misfit shows.
    ``inconsistent`` if any event's T exceeds the χ² ``gof_quantile``. A status, never a ranking rule.
    """
    from scipy.stats import chi2

    out = {"inconsistent": False, "chi2": [], "dof": [], "p": []}
    for rd, k_ in zip(ridges, assignment):
        if k_ < 0 or len(rd.t_s) == 0:
            out["chi2"].append(None), out["dof"].append(None), out["p"].append(None)
            continue
        ratio, _, _, _ = modeled_ratio(xyz, k_, ts, start_time_s + dt_s + rd.t_s, c)
        _, f0, _, _ = _event_nll(rd, ratio[None, :], c, p)
        ok = np.isfinite(ratio)
        model = np.where(ok, f0[0] * ratio, np.nan)
        sw2, sm2 = _ridge_sigma2(rd, model[None, :], ratio[None, :], c, p)
        r = rd.f_hz[ok] - model[ok]
        T = float(np.sum(r ** 2 / (sw2[0][ok] + sm2[0][ok])) / rd.kappa)
        dof = max(float(ok.sum()) / rd.kappa, 1.0)
        pv = float(chi2.sf(T, dof))
        out["chi2"].append(T), out["dof"].append(dof), out["p"].append(pv)
        out["inconsistent"] |= pv < 1.0 - p.gof_quantile
    return out


def ridge_scatter_ref(t_s: NDArray, f_hz: NDArray, decimate_s: float = 0.5) -> float:
    """Model-free ridge scatter σ_ref (Hz) for the absolute-fit check (P0.4).

    Second differences of the decimated ridge remove the slowly varying Doppler curve. With AR(1) ridge noise
    (lag-1 correlation ρ) var(Δ²f) = σ²(6 − 8ρ + 2ρ²) and the lag-1 autocorrelation of Δ²f is
    (−4 + 7ρ − 4ρ² + ρ³)/(6 − 8ρ + 2ρ²); ρ is solved from the observed value and σ follows. Robust (MAD) scale.
    """
    return ridge_white_stats(t_s, f_hz, decimate_s)[0]


def ridge_white_stats(t_s: NDArray, f_hz: NDArray, decimate_s: float = 0.5) -> tuple[float, float]:
    """(σ_white, ρ₁) of the decimated ridge, model-free (see ridge_scatter_ref); (NaN, NaN) if too short.

    ρ₁ is the AR(1) lag-1 correlation solved on a 0–0.95 grid, so the effective-N factor (1 + ρ)/(1 − ρ)
    that P1.1 derives from it is hypothesis-independent.
    """
    t, f = np.asarray(t_s, float), np.asarray(f_hz, float)
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    if len(t) < 8:
        return float("nan"), float("nan")
    b = np.floor((t - t.min()) / decimate_s).astype(np.int64)
    ub, inv = np.unique(b, return_inverse=True)
    fd = np.array([np.median(f[inv == k]) for k in range(len(ub))])
    d2 = np.diff(fd, 2)
    if len(d2) < 6:
        return float("nan"), float("nan")
    mad = 1.4826 * np.median(np.abs(d2 - np.median(d2)))
    x = d2 - d2.mean()
    r1_obs = float(np.sum(x[1:] * x[:-1]) / max(np.sum(x * x), 1e-12))
    rho = np.linspace(0.0, 0.95, 96)
    den = 6 - 8 * rho + 2 * rho ** 2
    r1 = (-4 + 7 * rho - 4 * rho ** 2 + rho ** 3) / den
    k = int(np.argmin(np.abs(r1 - r1_obs)))
    return float(mad / np.sqrt(den[k])), float(rho[k])


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
