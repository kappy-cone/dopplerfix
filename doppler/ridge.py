"""Ridge extraction: a continuity-constrained dynamic-programming path through the
spectrogram, weak-column rejection, sub-bin refinement and a harmonic check.

The DP maximises sum(excess_dB) − λ·|Δf| with per-step frequency moves limited
to [−max_down, +max_up]: Doppler curves only descend (a straight pass is
monotonic; turns/climbs add mild wobble), which keeps the path from hopping
to another harmonic or another aircraft.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import median_filter
from scipy.signal import savgol_filter

from .audio import Spectrogram, background_db
from .detect import EventWindow


@dataclass(frozen=True)
class RidgeParams:
    max_up_hz_per_s: float = 4.0
    max_down_hz_per_s: float = 60.0
    jump_penalty_db_per_hz: float = 0.5
    weak_db: float = 12.0          # reject columns weaker than this above the per-bin median
    outlier_hz: float = 3.0        # reject points this far from a 2 s running median
    min_points: int = 50
    min_ridge_s: float = 5.0       # strong ridge points must cover at least this much time (hop-independent)
    harmonic_db: float = 8.0       # median excess along f/n that counts as a sub-harmonic being present
    max_ridges: int = 4            # per window (overlapping flyovers)
    mask_halfwidth_hz: float = 4.0 # mask around an extracted ridge's harmonic family
    guide_hz: float = 3.0          # guided re-extraction half-width around a provisional fit
    guided_passes: int = 2
    seed_mode: str = "hough"       # hough | dp: how each flyover is first located in a window
    hough_dlogf: float = 0.008     # log-frequency step
    hough_dt_s: float = 0.5
    hough_cap_db: float = 25.0
    hough_harmonics: int = 3
    hough_dilate_bins: int = 3
    hough_v_range_mps: tuple = (35.0, 260.0)
    hough_v_step: float = 0.12     # log step
    hough_tau_range_s: tuple = (2.0, 80.0)  # d / v
    hough_tau_step: float = 0.3    # log step
    hough_t0_margin_s: float = 20.0
    hough_nms_s: float = 4.0
    handoff_harmonics: bool = False  # U19 arm h1: on a short guided ridge, try the seed's harmonics 2..hough_harmonics
    handoff_continue: bool = False   # U19 arm h2: if still none, mask the seed's family and keep peeling the window
    floor_mode: str = "freq"       # see audio.background_db
    floor_halfwidth_hz: float = 20.0

    @classmethod
    def from_dict(cls, d: dict) -> "RidgeParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Ridge:
    """Ridge points on the received-time axis (recording-relative)."""

    t_s: NDArray
    f_hz: NDArray
    excess_db: NDArray
    noise_hz: float                 # robust scatter about a local smooth
    band_hz: tuple[float, float]
    subharmonic: int = 1            # >1: energy found at f/subharmonic, ridge is likely a harmonic
    subharmonic_excess_db: float = float("nan")

    def __len__(self) -> int:
        return len(self.t_s)


def _dp_path(Z: NDArray, up: int, down: int, pen_per_bin: float) -> NDArray:
    """Max-score path through Z (F, T), moves limited to [−down, +up] bins per step."""
    F, T = Z.shape
    score = Z[:, 0].astype(np.float64).copy()
    back = np.zeros((F, T), dtype=np.int16)
    shifts = np.arange(-down, up + 1)
    for t in range(1, T):
        best = np.full(F, -np.inf)
        arg = np.zeros(F, dtype=np.int16)
        for s in shifts:  # current bin j came from previous bin j - s
            prev = np.full(F, -np.inf)
            if s >= 0:
                prev[s:] = score[: F - s]
            else:
                prev[:s] = score[-s:]
            cand = prev - pen_per_bin * abs(s)
            better = cand > best
            best[better] = cand[better]
            arg[better] = s
        score = best + Z[:, t]
        back[:, t] = arg
    path = np.empty(T, dtype=np.int64)
    path[-1] = int(np.argmax(score))
    for t in range(T - 1, 0, -1):
        path[t - 1] = path[t] - back[path[t], t]
    return path


def _parabolic(y_m: NDArray, y_0: NDArray, y_p: NDArray) -> NDArray:
    den = y_m - 2 * y_0 + y_p
    with np.errstate(divide="ignore", invalid="ignore"):
        off = np.where(den < 0, 0.5 * (y_m - y_p) / den, 0.0)
    return np.clip(off, -0.5, 0.5)


def robust_std(x: NDArray) -> float:
    return float(1.4826 * np.median(np.abs(x - np.median(x)))) if len(x) else float("nan")


def extract_ridge(spec: Spectrogram, window: EventWindow, params: RidgeParams = RidgeParams(),
                  floor_db: NDArray | None = None) -> Ridge | None:
    """The dominant descending ridge inside ``window``; None if too few strong points."""
    r = extract_ridges(spec, window, params, floor_db, max_ridges=1)
    return r[0] if r else None


def extract_ridges(spec: Spectrogram, window: EventWindow, params: RidgeParams = RidgeParams(),
                   floor_db: NDArray | None = None, max_ridges: int | None = None) -> list[Ridge]:
    """Successive descending ridges inside ``window`` (overlapping flyovers).

    After each ridge, the path and its harmonic family (f·k/n) are masked out and
    the DP is rerun, until no ridge with ``min_points`` strong points remains or
    ``max_ridges`` is reached. Ridges come out strongest first.
    """
    max_ridges = params.max_ridges if max_ridges is None else max_ridges
    floor = background_db(spec, params.floor_mode, params.floor_halfwidth_hz) if floor_db is None \
        else np.asarray(floor_db)
    if floor.ndim == 1:
        floor = floor[:, None]
    ti = np.flatnonzero((spec.t_s >= window.start_s) & (spec.t_s <= window.end_s))
    fi = np.flatnonzero((spec.f_hz >= window.band_hz[0]) & (spec.f_hz <= window.band_hz[1]))
    if len(ti) < 3 or len(fi) < 3:
        return []
    full_Z = spec.power_db - floor
    Z = np.clip(full_Z[np.ix_(fi, ti)], -10.0, 60.0)
    df, hop = spec.df_hz, spec.hop_s
    up = max(1, int(np.ceil(params.max_up_hz_per_s * hop / df)))
    down = max(1, int(np.ceil(params.max_down_hz_per_s * hop / df)))
    cols = np.arange(len(ti))
    out: list[Ridge] = []
    while len(out) < max_ridges:
        path = _dp_path(Z, up, down, params.jump_penalty_db_per_hz * df)
        ridge = _finish_ridge(spec, full_Z, fi, ti, path, Z[path, cols], params, window)
        # mask this path's harmonic family before looking for the next ridge
        mask_family(Z, spec.f_hz[fi], cols, spec.f_hz[fi[path]], params.mask_halfwidth_hz,
                    subharmonics=(1, 2, 3))
        if ridge is None:
            break
        out.append(ridge)
    return out


def family_ratios(subharmonics: tuple[int, ...] = (1,), max_k: int = 8) -> tuple[float, ...]:
    """Frequency ratios of a ridge's harmonic family: k/n for k ≤ max_k and each possible
    harmonic number n of the ridge itself (n = 1 when the ridge is the fundamental)."""
    return tuple(sorted({k / n for n in subharmonics for k in range(1, max_k * n + 1) if k / n <= max_k}))


def mask_family(Z: NDArray, f_axis: NDArray, cols: NDArray, f_curve: NDArray, halfwidth_hz: float,
                fill: float = -10.0, subharmonics: tuple[int, ...] = (1,)) -> None:
    """In-place: blank ±halfwidth around the harmonic family of f_curve in columns ``cols`` of Z.

    Only the true family is masked (integer multiples of the fundamental), so a
    second aircraft with a nearby tone is not erased along with the first.
    """
    df = f_axis[1] - f_axis[0]
    halfw = max(1, int(round(halfwidth_hz / df)))
    ok_curve = np.isfinite(f_curve)
    for r in family_ratios(subharmonics):
        b = np.rint((f_curve * r - f_axis[0]) / df)
        for o in range(-halfw, halfw + 1):
            bb = b + o
            ok = ok_curve & (bb >= 0) & (bb < len(f_axis))
            Z[bb[ok].astype(int), cols[ok]] = fill


def guided_ridge(spec: Spectrogram, Zsub: NDArray, fi: NDArray, ti: NDArray, f_model: NDArray,
                 params: RidgeParams, window: EventWindow, full_Z: NDArray) -> Ridge | None:
    """Re-extract ridge points as the strongest bin within ±guide_hz of a model curve, per frame.

    ``Zsub`` is the (possibly masked) excess on (fi, ti); ``f_model`` has one value per ti.
    Used after a provisional fit so a ridge that crossed another flyover is recovered whole.
    """
    df = spec.df_hz
    g = max(1, int(round(params.guide_hz / df)))
    centre = np.rint((f_model - spec.f_hz[fi[0]]) / df).astype(int)
    offs = np.arange(-g, g + 1)
    rows = centre[None, :] + offs[:, None]                      # (2g+1, T)
    valid = (rows >= 0) & (rows < len(fi))
    vals = np.where(valid, Zsub[np.clip(rows, 0, len(fi) - 1), np.arange(len(ti))[None, :]], -np.inf)
    best = np.argmax(vals, axis=0)
    path = np.clip(rows[best, np.arange(len(ti))], 0, len(fi) - 1)
    exc = vals[best, np.arange(len(ti))]
    return _finish_ridge(spec, full_Z, fi, ti, path, exc, params, window)


def harmonic_handoff(spec: Spectrogram, Zsub: NDArray, fi: NDArray, ti: NDArray, f_seed: NDArray,
                     params: RidgeParams, window: EventWindow, full_Z: NDArray) -> Ridge | None:
    """U19 arm h1: the guided ridge around m × the seed curve, m = 2 .. hough_harmonics, with the most points.

    For a seed found through its harmonics while its own line is weak (the seed statistic sums
    ``hough_harmonics`` harmonics). Ties go to the lower m. None if no harmonic gives a ridge.
    """
    best = None
    for m in range(2, params.hough_harmonics + 1):
        g = guided_ridge(spec, Zsub, fi, ti, m * f_seed, params, window, full_Z)
        if g is not None and (best is None or len(g) > len(best)):
            best = g
    return best


def _finish_ridge(spec, full_Z, fi, ti, path, exc, params: RidgeParams, window) -> Ridge | None:
    """Weak-column rejection, sub-bin refinement, continuity outliers, noise, harmonic check."""
    df, hop = spec.df_hz, spec.hop_s
    keep = exc >= params.weak_db
    bins = fi[path]
    P = spec.power_db
    inner = (bins > 0) & (bins < len(spec.f_hz) - 1)
    off = np.zeros(len(bins))
    off[inner] = _parabolic(P[bins[inner] - 1, ti[inner]], P[bins[inner], ti[inner]], P[bins[inner] + 1, ti[inner]])
    f = spec.f_hz[bins] + off * df
    t = spec.t_s[ti]
    if keep.sum() >= 5:
        k = max(3, int(round(2.0 / hop)) | 1)
        fk = f[keep]
        med = median_filter(fk, size=k, mode="nearest")
        idx = np.flatnonzero(keep)
        keep[idx[np.abs(fk - med) > params.outlier_hz]] = False
    if keep.sum() < params.min_points or keep.sum() * hop < params.min_ridge_s:
        return None
    t, f, exc = t[keep], f[keep], exc[keep]
    wl = min(len(f) - (1 - len(f) % 2), max(5, int(round(3.0 / hop)) | 1))
    noise = robust_std(f - savgol_filter(f, wl, 2)) if wl >= 5 else float("nan")
    sub, sub_exc = harmonic_check(full_Z, spec.f_hz, spec.t_s, t, f, params.harmonic_db)
    return Ridge(t_s=t, f_hz=f, excess_db=exc, noise_hz=noise, band_hz=tuple(window.band_hz),
                 subharmonic=sub, subharmonic_excess_db=sub_exc)


def harmonic_check(full_Z: NDArray, f_axis: NDArray, t_axis: NDArray, t: NDArray, f: NDArray,
                   weak_db: float, candidates: tuple[int, ...] = (2, 3)) -> tuple[int, float]:
    """Look for energy at f/2 and f/3 along the ridge. Returns (n, excess) for the
    largest n whose subharmonic is consistently present, else (1, nan)."""
    ti = np.searchsorted(t_axis, t).clip(0, len(t_axis) - 1)
    best_n, best_exc = 1, float("nan")
    for n in candidates:
        fq = f / n
        if fq.min() < f_axis[0] + 2 * (f_axis[1] - f_axis[0]):
            continue
        fi = np.searchsorted(f_axis, fq).clip(1, len(f_axis) - 1)
        # Take the max of the two neighbouring bins (sub-harmonic may fall between bins).
        vals = np.maximum(full_Z[fi, ti], full_Z[fi - 1, ti])
        med = float(np.median(vals))
        if med >= weak_db:
            best_n, best_exc = n, med
    return best_n, best_exc


# ---------------------------------------------------------------------------
# Doppler-curve matched filter ("Hough"): robust seeds where ridges cross
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HoughCandidate:
    f0_hz: float
    v_mps: float
    d_m: float
    t0_s: float      # emission CPA time, recording-relative
    score: float


def doppler_hough(spec: Spectrogram, Zsub: NDArray, fi: NDArray, ti: NDArray, params: RidgeParams,
                  c: float, n_best: int = 5) -> list[HoughCandidate]:
    """Matched filter of straight-pass Doppler curves over a (masked) excess window.

    In log-frequency the source tone f0 is a shift and t0 is a time shift, so for
    each curve shape (v, d) the evidence for every (f0, t0) is one 2-D
    cross-correlation of the harmonic-summed, dilated excess with the shape's
    curve image. A ridge that crosses another flyover is still scored along its
    own whole curve, which a frame-by-frame tracker cannot do.
    """
    import scipy.fft as sfft
    from scipy.ndimage import maximum_filter

    from .physics import straight_freq_received

    dlf = params.hough_dlogf
    f_sub = spec.f_hz[fi]
    lf = np.arange(np.log(f_sub[0]), np.log(f_sub[-1]), dlf)
    # log-f resampling: max of the two nearest linear bins
    pos = (np.exp(lf) - f_sub[0]) / spec.df_hz
    j0 = np.clip(np.floor(pos).astype(int), 0, len(fi) - 1)
    j1 = np.clip(j0 + 1, 0, len(fi) - 1)
    E = np.clip(np.maximum(Zsub[j0], Zsub[j1]), 0.0, params.hough_cap_db)
    # time decimation
    nd = max(1, int(round(params.hough_dt_s / spec.hop_s)))
    T = E.shape[1] // nd
    if T < 4:
        return []
    E = E[:, : T * nd].reshape(len(lf), T, nd).mean(axis=2)
    t_dec = spec.t_s[ti[: T * nd]].reshape(T, nd).mean(axis=1)
    dt = t_dec[1] - t_dec[0]
    # harmonic sum (fundamental hypothesis at row i)
    H = np.zeros_like(E)
    for k in range(1, params.hough_harmonics + 1):
        sh = int(round(np.log(k) / dlf))
        if sh < len(lf):
            H[: len(lf) - sh] += E[sh:]
    H = maximum_filter(H, size=(params.hough_dilate_bins, 3), mode="constant")

    v_grid = np.exp(np.arange(np.log(params.hough_v_range_mps[0]), np.log(params.hough_v_range_mps[1]),
                              params.hough_v_step))
    tau_grid = np.exp(np.arange(np.log(params.hough_tau_range_s[0]), np.log(params.hough_tau_range_s[1]),
                                params.hough_tau_step))
    taus = np.arange(-T, T + 1) * dt          # received time relative to the emission CPA time t0
    shapes = []
    for v in v_grid:
        for tau in tau_grid:
            s_ = np.rint(np.log(straight_freq_received(taus, 1.0, v, v * tau, 0.0, c)) / dlf).astype(int)
            shapes.append((v, v * tau, s_))
    Kr = max(sh[2].max() - sh[2].min() + 1 for sh in shapes)
    Kc = len(taus)
    F = len(lf)
    # Correlation via one FFT of H reused for every shape: corr(H, K) = conv(H, flip(K)).
    fshape = (sfft.next_fast_len(F + Kr - 1), sfft.next_fast_len(T + Kc - 1))
    FH = sfft.rfft2(H, fshape)
    m_lo, m_hi = -int(params.hough_t0_margin_s / dt), T + int(params.hough_t0_margin_s / dt)
    cands = []
    cols_t = np.arange(Kc)
    for v, d, s_ in shapes:
        s_min = s_.min()
        Kf = np.zeros((Kr, Kc))
        Kf[Kr - 1 - (s_ - s_min), Kc - 1 - cols_t] = 1.0   # flipped kernel
        out = sfft.irfft2(FH * sfft.rfft2(Kf, fshape), fshape)[: F + Kr - 1, : T + Kc - 1]
        # out[a, b] ↔ f0 row i = a − Kr + 1 − s_min, t0 index m = b − Kc + 1 + T
        a_idx = np.arange(out.shape[0]) - Kr + 1 - s_min
        b_idx = np.arange(out.shape[1]) - Kc + 1 + T
        okr = (a_idx >= 0) & (a_idx < F)
        okc = (b_idx >= m_lo) & (b_idx <= m_hi)
        sub = out[np.ix_(okr, okc)]
        if sub.size == 0:
            continue
        ia, ib = np.unravel_index(np.argmax(sub), sub.shape)
        i, m = a_idx[okr][ia], b_idx[okc][ib]
        cands.append(HoughCandidate(float(np.exp(lf[i])), float(v), float(d), float(t_dec[0] + m * dt),
                                    float(sub[ia, ib])))
    cands.sort(key=lambda h: -h.score)
    # non-maximum suppression in (log f0, t0)
    kept: list[HoughCandidate] = []
    for h in cands:
        if all(abs(h.t0_s - k.t0_s) > params.hough_nms_s or abs(np.log(h.f0_hz / k.f0_hz)) > 0.03 for k in kept):
            kept.append(h)
        if len(kept) >= n_best:
            break
    return kept
