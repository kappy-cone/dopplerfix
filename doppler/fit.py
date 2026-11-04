"""Per-event straight-line fit of (f0, v, d, t0) to a ridge on the received-time axis.

The model is exact for a straight, level, constant-speed pass with propagation
delay (physics.straight_freq_received). ``t0`` is the *emission* CPA time; the
CPA sound arrives at ``t0 + d/c``. Curved tracks are only approximated here —
the scorer uses the general track model, and these fits are for seeding and
pruning.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import uniform_filter1d
from scipy.optimize import least_squares

from . import physics as ph
from .audio import Spectrogram, SpectrogramParams, background_db, compute_spectrogram
from .classify import ClassFeatures, ClassifyParams, broadband_rise_db, decide, harmonic_count
from .detect import Detection
from .detect import EventWindow
from .ridge import (Ridge, RidgeParams, _dp_path, _finish_ridge, doppler_hough, guided_ridge, mask_family,
                    robust_std)

PARAM_NAMES = ("f0_hz", "v_mps", "d_m", "t0_s")  # (+ "drift_per_s" when the drift prior is on)


@dataclass(frozen=True)
class FitParams:
    v_bounds_mps: tuple[float, float] = (10.0, 320.0)
    d_bounds_m: tuple[float, float] = (20.0, 30000.0)
    t0_margin_s: float = 60.0
    corr_inflation: float | None = None   # None -> from residual autocorrelation (>= sqrt(window/hop))
    outlier_sigma: float = 4.0
    f0_drift_prior_per_s: float | None = 1e-4  # 1-σ fractional source-tone drift rate; None disables
    min_descent_frac: float = 0.02        # (f_start − f_end)/f needed to call it a flyover

    @classmethod
    def from_dict(cls, d: dict) -> "FitParams":
        kw = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k in ("v_bounds_mps", "d_bounds_m"):
            if k in kw:
                kw[k] = tuple(kw[k])
        return cls(**kw)


@dataclass
class EventFit:
    """Fitted flyover. Times are recording-relative seconds."""

    window: EventWindow
    ridge: Ridge
    f0_hz: float
    v_mps: float
    d_m: float
    t0_s: float                    # emission time of CPA
    t_cpa_received_s: float        # t0 + d/c
    sigma: dict[str, float]        # 1-σ, inflated for correlated (overlapping) frames
    cov: NDArray = field(repr=False)
    rms_hz: float = float("nan")   # residual RMS of inliers
    n_points: int = 0
    c: float = ph.DEFAULT_C
    converged: bool = True
    drift_per_s: float = 0.0       # fitted fractional source-tone drift rate (nuisance)
    cls: int = 2                   # aircraft_types.PROP / JET / UNKNOWN (set by characterize_audio)
    class_features: object = None  # classify.ClassFeatures
    noise_hz: float = float("nan") # per-point ridge scatter about the fit (white part)

    @property
    def f_pre_hz(self) -> float:
        return ph.straight_asymptotes(self.f0_hz, self.v_mps, self.c)[0]

    @property
    def f_post_hz(self) -> float:
        return ph.straight_asymptotes(self.f0_hz, self.v_mps, self.c)[1]

    @property
    def coverage(self) -> tuple[float, float]:
        """Ridge extent before / after the received CPA, in units of the transition time d/v.

        Below ~2 on a side the plateau on that side was not observed (clip edge or
        overlap), so v and d are poorly constrained: widen downstream tolerances.
        """
        tau = self.d_m / max(self.v_mps, 1e-6)
        return ((self.t_cpa_received_s - self.ridge.t_s[0]) / tau, (self.ridge.t_s[-1] - self.t_cpa_received_s) / tau)

    def model(self, t_s: NDArray, include_drift: bool = True) -> NDArray:
        f = ph.straight_freq_received(t_s, self.f0_hz, self.v_mps, self.d_m, self.t0_s, self.c)
        if include_drift:
            f = f * (1 + self.drift_per_s * (np.asarray(t_s) - self.t_cpa_received_s))
        return f


def correlation_factor(res: NDArray, max_lag: int | None = None) -> float:
    """1 + 2·Σρ_k over lags until the autocorrelation first drops to ≤ 0.

    The variance inflation for a mean of correlated samples; slow systematic
    residuals (engine-tone wander, unmodelled turns) make it large, which keeps
    the reported σ honest. Assumes roughly uniform sampling of the ridge.
    """
    x = res - res.mean()
    n = len(x)
    if n < 8 or not np.any(x):
        return 1.0
    max_lag = max_lag or n // 4
    spec = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:max_lag + 1]
    rho = ac / ac[0]
    stop = np.flatnonzero(rho[1:] <= 0)
    k = (stop[0] + 1) if len(stop) else len(rho)
    return float(1.0 + 2.0 * rho[1:k].sum())


def initial_guess(t: NDArray, f: NDArray, c: float) -> tuple[float, float, float, float]:
    """Closed-form seed: plateaus -> (v, f0); crossing of f0 -> t_cpa; max slope -> d."""
    n = len(f)
    q = max(3, n // 7)
    f_pre, f_post = float(np.median(f[:q])), float(np.median(f[-q:]))
    if f_pre <= f_post:
        f_pre, f_post = f_post * 1.01, f_post
    v = ph.invert_speed(f_pre, f_post, c)
    f0 = ph.invert_f0(f_pre, f_post)
    fs = uniform_filter1d(f, size=max(3, n // 40), mode="nearest")
    below = np.flatnonzero(fs < f0)
    t_cpa = float(t[below[0]]) if len(below) else float(np.median(t))
    slope = np.gradient(fs, t)
    near = np.abs(t - t_cpa) < 0.25 * (t[-1] - t[0]) + 1e-9
    rate = float(np.max(-slope[near])) if near.any() else float(np.max(-slope))
    d = ph.invert_distance(f0, v, max(rate, 1e-3), c)
    return f0, v, d, t_cpa - d / c


def fit_ridge(ridge: Ridge, window: EventWindow, params: FitParams = FitParams(), c: float = ph.DEFAULT_C,
              hop_s: float = 0.1, window_s: float = 1.0) -> EventFit:
    """Fit (f0, v, d, t0) to a ridge.

    Stage 1: bounded robust (soft-L1) least squares, multi-start in d.
    Stage 2: on inliers, a whitened fit adding a source-drift nuisance with a
    prior; covariance from the whitened Jacobian, with per-point noise inflated
    for correlated (overlapping-frame, slowly wandering) residuals.
    """
    t, f = ridge.t_s, ridge.f_hz
    vmax = min(params.v_bounds_mps[1], 0.95 * c)
    lb = np.array([0.5 * f.min(), params.v_bounds_mps[0], params.d_bounds_m[0], t[0] - params.t0_margin_s])
    ub = np.array([2.0 * f.max(), vmax, params.d_bounds_m[1], t[-1] + params.t0_margin_s])
    x_scale = np.array([10.0, 10.0, 200.0, 5.0])

    def resid(x, tt, ff):
        return ph.straight_freq_received(tt, x[0], x[1], x[2], x[3], c) - ff

    f0, v, d, t0 = initial_guess(t, f, c)
    noise = ridge.noise_hz if np.isfinite(ridge.noise_hz) and ridge.noise_hz > 0 else 0.5
    best = None
    for dmul in (1.0, 0.5, 2.0):
        dd = d * dmul
        x0 = np.clip([f0, v, dd, t0 + (d - dd) / c], lb + 1e-6 * (ub - lb), ub - 1e-6 * (ub - lb))
        r = least_squares(resid, x0, bounds=(lb, ub), x_scale=x_scale, loss="soft_l1",
                          f_scale=max(noise, 0.2), args=(t, f))
        if best is None or r.cost < best.cost:
            best = r
    # Outlier rejection against the robust stage-1 fit.
    res = resid(best.x, t, f)
    s = max(robust_std(res), 1e-3)
    inl = np.abs(res) <= params.outlier_sigma * s
    ti, fi = t[inl], f[inl]
    res_in = res[inl]
    s_in = max(float(np.std(res_in)), 1e-3)
    if params.corr_inflation is not None:
        infl2 = params.corr_inflation**2
    else:
        infl2 = max(window_s / hop_s, correlation_factor(res_in), 1.0)
    sigma_eff = s_in * np.sqrt(infl2)

    # Stage 2: whitened fit with a source-tone drift nuisance β under a Gaussian prior.
    # f(t) = f0·(1 + β·(t − t_cpa_r))·ratio(t). Drift is degenerate with v (both tilt
    # pre vs post plateaus), so the prior is what makes σ_v honest about RPM wander.
    sb = params.f0_drift_prior_per_s
    use_drift = sb is not None and sb > 0

    def resid_w(x):
        beta = x[4] if use_drift else 0.0
        tref = x[3] + x[2] / c
        m = ph.straight_freq_received(ti, x[0], x[1], x[2], x[3], c) * (1 + beta * (ti - tref))
        r_ = (m - fi) / sigma_eff
        return np.r_[r_, beta / sb] if use_drift else r_

    if use_drift:
        lb2, ub2 = np.r_[lb, -50 * sb], np.r_[ub, 50 * sb]
        x0 = np.r_[best.x, 0.0]
        xs = np.r_[x_scale, sb]
    else:
        lb2, ub2, x0, xs = lb, ub, best.x, x_scale
    r = least_squares(resid_w, x0, bounds=(lb2, ub2), x_scale=xs)
    J = r.jac
    try:
        cov = np.linalg.inv(J.T @ J)
    except np.linalg.LinAlgError:
        cov = np.full((len(x0), len(x0)), np.nan)
    sig = dict(zip(PARAM_NAMES, np.sqrt(np.clip(np.diag(cov)[:4], 0, None))))
    f0, v, d, t0 = map(float, r.x[:4])
    drift = float(r.x[4]) if use_drift else 0.0
    if use_drift:
        sig["drift_per_s"] = float(np.sqrt(max(cov[4, 4], 0)))
    n_data = int(inl.sum())
    return EventFit(
        window=window, ridge=ridge, f0_hz=f0, v_mps=v, d_m=d, t0_s=t0, t_cpa_received_s=t0 + d / c,
        sigma=sig, cov=cov, rms_hz=float(np.sqrt(np.mean((r.fun[:n_data] * sigma_eff) ** 2))),
        n_points=n_data, c=c, converged=bool(r.success), drift_per_s=drift, noise_hz=s_in,
    )


def _descent(ridge: Ridge) -> float:
    q = max(3, len(ridge) // 7)
    return float((np.median(ridge.f_hz[:q]) - np.median(ridge.f_hz[-q:])) / np.median(ridge.f_hz))


def characterize_window(spec: Spectrogram, full_Z: NDArray, window: EventWindow,
                        ridge_params: RidgeParams = RidgeParams(), fit_params: FitParams = FitParams(),
                        c: float = ph.DEFAULT_C) -> list[EventFit]:
    """Peel flyovers off one window, strongest first.

    Each pass: DP ridge on the masked excess → provisional robust fit →
    re-extract the ridge along the fitted curve (recovers segments lost where
    two flyovers cross) → final fit → mask the fitted curve's harmonic family.
    """
    rp, fp = ridge_params, fit_params
    ti = np.flatnonzero((spec.t_s >= window.start_s) & (spec.t_s <= window.end_s))
    fi = np.flatnonzero((spec.f_hz >= window.band_hz[0]) & (spec.f_hz <= window.band_hz[1]))
    if len(ti) < 3 or len(fi) < 3:
        return []
    Zw = np.clip(full_Z[np.ix_(fi, ti)], -10.0, 60.0)
    df, hop = spec.df_hz, spec.hop_s
    up = max(1, int(np.ceil(rp.max_up_hz_per_s * hop / df)))
    down = max(1, int(np.ceil(rp.max_down_hz_per_s * hop / df)))
    cols = np.arange(len(ti))
    t_win = spec.t_s[ti]
    f_sub = spec.f_hz[fi]
    fits: list[EventFit] = []
    for _ in range(rp.max_ridges + 2):
        if len(fits) >= rp.max_ridges:
            break
        if (Zw.max(axis=0) >= rp.weak_db).sum() < rp.min_points:
            break  # nothing strong left to explain
        if rp.seed_mode == "hough":
            seeds = doppler_hough(spec, Zw, fi, ti, rp, c, n_best=1)
            if not seeds:
                break
            h = seeds[0]
            f_seed = ph.straight_freq_received(t_win, h.f0_hz, h.v_mps, h.d_m, h.t0_s, c)
            g = guided_ridge(spec, Zw, fi, ti, f_seed, rp, window, full_Z)
            if g is None:
                break
            est = fit_ridge(g, window, fp, c, hop, spec.window_s)
            for _pass in range(rp.guided_passes):
                g2 = guided_ridge(spec, Zw, fi, ti, est.model(t_win), rp, window, full_Z)
                if g2 is None:
                    break
                est = fit_ridge(g2, window, fp, c, hop, spec.window_s)
            if _descent(est.ridge) >= fp.min_descent_frac or window.manual:
                fits.append(est)
            subs = (1, est.ridge.subharmonic) if est.ridge.subharmonic > 1 else (1,)
            mask_family(Zw, f_sub, cols, est.model(t_win), rp.mask_halfwidth_hz, subharmonics=subs)
            mask_family(Zw, f_sub, cols, f_seed, rp.mask_halfwidth_hz, subharmonics=subs)
            continue
        path = _dp_path(Zw, up, down, rp.jump_penalty_db_per_hz * df)
        raw = _finish_ridge(spec, full_Z, fi, ti, path, Zw[path, cols], rp, window)
        if raw is None:
            break
        if _descent(raw) < fp.min_descent_frac and not window.manual:
            mask_family(Zw, f_sub, cols, f_sub[path], rp.mask_halfwidth_hz, subharmonics=(1, 2, 3))
            continue
        est = fit_ridge(raw, window, fp, c, hop, spec.window_s)
        for _pass in range(rp.guided_passes):
            g = guided_ridge(spec, Zw, fi, ti, est.model(t_win), rp, window, full_Z)
            if g is None:
                break
            est = fit_ridge(g, window, fp, c, hop, spec.window_s)
        if _descent(est.ridge) >= fp.min_descent_frac or window.manual:
            fits.append(est)
        mask_family(Zw, f_sub, cols, est.model(t_win), rp.mask_halfwidth_hz)
        mask_family(Zw, f_sub, cols, f_sub[path], rp.mask_halfwidth_hz, subharmonics=(1, 2, 3))
    return fits


def characterize_events(spec: Spectrogram, windows: list[EventWindow], ridge_params: RidgeParams = RidgeParams(),
                        fit_params: FitParams = FitParams(), c: float = ph.DEFAULT_C) -> list[EventFit]:
    """Fits for every flyover in every window (several per window when flyovers overlap).
    Ridges that do not descend are dropped. Sorted by received CPA time."""
    full_Z = spec.power_db - background_db(spec, ridge_params.floor_mode, ridge_params.floor_halfwidth_hz)
    fits = []
    for w in windows:
        fits += characterize_window(spec, full_Z, w, ridge_params, fit_params, c)
    return sorted(fits, key=lambda e: e.t_cpa_received_s)


def characterize_audio(x: NDArray, fs_hz: float, detection: Detection,
                       spec_params: SpectrogramParams = SpectrogramParams(),
                       ridge_params: RidgeParams = RidgeParams(), jet_ridge_params: RidgeParams | None = None,
                       fit_params: FitParams = FitParams(), classify_params: ClassifyParams = ClassifyParams(),
                       c: float = ph.DEFAULT_C, jet_spec_params: SpectrogramParams | None = None) -> list[EventFit]:
    """Per-window spectrograms (memory ∝ window), prop- and jet-band peeling, classification.

    Jet-band tones that coincide with a prop event (same CPA time and speed) are
    that propeller's high harmonics and are dropped.
    """
    from dataclasses import replace

    cp = classify_params
    jrp = jet_ridge_params or ridge_params
    quiet = float(np.percentile(detection.broadband_db, cp.broadband_percentile))
    out: list[EventFit] = []
    for w in detection.windows:
        pad = spec_params.window_s
        sp = compute_spectrogram(x, fs_hz, spec_params, (max(0.0, w.start_s - pad), w.end_s + pad))
        full_Z = sp.power_db - background_db(sp, ridge_params.floor_mode, ridge_params.floor_halfwidth_hz)
        band = w.band_hz if w.manual else cp.prop_band_hz
        prop = characterize_window(sp, full_Z, replace(w, band_hz=tuple(band)), ridge_params, fit_params, c)
        jet = []
        if jet_spec_params is not None:
            sj = compute_spectrogram(x, fs_hz, jet_spec_params, (max(0.0, w.start_s - pad), w.end_s + pad))
            zj = sj.power_db - background_db(sj, jrp.floor_mode, jrp.floor_halfwidth_hz)
            jb = (cp.jet_band_hz[0], min(cp.jet_band_hz[1], sj.f_hz[-1]))
            jet = characterize_window(sj, zj, replace(w, band_hz=jb), jrp, fit_params, c)
            jet = [j for j in jet if not any(
                abs(j.t_cpa_received_s - q.t_cpa_received_s) < cp.dedupe_dt_s
                and abs(j.v_mps / q.v_mps - 1) < cp.dedupe_v_frac for q in prop)]
        for band_name, fits in (("prop", prop), ("jet", jet)):
            for e in fits:
                zz, ss = (full_Z, sp) if band_name == "prop" else (zj, sj)
                nh = harmonic_count(zz, ss.f_hz, ss.t_s, e.ridge.t_s, e.ridge.f_hz, cp.harmonic_weak_db,
                                    cp.max_harmonic)
                half = max(5.0, e.d_m / max(e.v_mps, 1.0))
                bb = broadband_rise_db(detection.t_s, detection.broadband_db, quiet, e.t_cpa_received_s, half)
                e.cls = decide(band_name, nh, bb, cp)
                e.class_features = ClassFeatures(band_name, nh, bb, e.cls)
                out.append(e)
    return sorted(out, key=lambda e: e.t_cpa_received_s)
