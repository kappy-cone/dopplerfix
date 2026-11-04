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

from .audio import Spectrogram
from .detect import EventWindow


@dataclass(frozen=True)
class RidgeParams:
    max_up_hz_per_s: float = 4.0
    max_down_hz_per_s: float = 60.0
    jump_penalty_db_per_hz: float = 0.5
    weak_db: float = 12.0          # reject columns weaker than this above the per-bin median
    outlier_hz: float = 3.0        # reject points this far from a 2 s running median
    min_points: int = 50

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
    """Extract the dominant descending ridge inside ``window``; None if too few strong points."""
    floor = spec.noise_floor_db() if floor_db is None else floor_db
    tm = (spec.t_s >= window.start_s) & (spec.t_s <= window.end_s)
    fm = (spec.f_hz >= window.band_hz[0]) & (spec.f_hz <= window.band_hz[1])
    fi = np.flatnonzero(fm)
    ti = np.flatnonzero(tm)
    if len(ti) < 3 or len(fi) < 3:
        return None
    full_Z = spec.power_db - floor[:, None]
    Z = np.clip(full_Z[np.ix_(fi, ti)], -10.0, 60.0)
    df, hop = spec.df_hz, spec.hop_s
    up = max(1, int(np.ceil(params.max_up_hz_per_s * hop / df)))
    down = max(1, int(np.ceil(params.max_down_hz_per_s * hop / df)))
    path = _dp_path(Z, up, down, params.jump_penalty_db_per_hz * df)

    cols = np.arange(len(ti))
    exc = Z[path, cols]
    keep = exc >= params.weak_db
    # Sub-bin refinement on the dB spectrum (interior bins only).
    bins = fi[path]
    P = spec.power_db
    tcols = ti
    inner = (bins > 0) & (bins < len(spec.f_hz) - 1)
    off = np.zeros(len(bins))
    off[inner] = _parabolic(P[bins[inner] - 1, tcols[inner]], P[bins[inner], tcols[inner]],
                            P[bins[inner] + 1, tcols[inner]])
    f = spec.f_hz[bins] + off * df
    t = spec.t_s[tcols]

    # Continuity outliers relative to a 2 s running median of the kept points.
    if keep.sum() >= 5:
        k = max(3, int(round(2.0 / hop)) | 1)
        fk = f[keep]
        med = median_filter(fk, size=k, mode="nearest")
        ok = np.abs(fk - med) <= params.outlier_hz
        idx = np.flatnonzero(keep)
        keep[idx[~ok]] = False
    if keep.sum() < params.min_points:
        return None
    t, f, exc = t[keep], f[keep], exc[keep]

    # Noise: scatter about a ~3 s local quadratic smooth.
    wl = min(len(f) - (1 - len(f) % 2), max(5, int(round(3.0 / hop)) | 1))
    noise = robust_std(f - savgol_filter(f, wl, 2)) if wl >= 5 else float("nan")

    sub, sub_exc = harmonic_check(full_Z, spec.f_hz, spec.t_s, t, f, params.weak_db)
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
