"""Doppler physics: straight-line flyover model, closed-form inversions, and the
general track-based predicted curve used for scoring.

Conventions
-----------
* ``t_e`` is emission time (when the aircraft made the sound), ``t_r`` is
  received time (when the microphone heard it): ``t_r = t_e + r(t_e)/c``.
* The Doppler ratio observed/source is ``c / (c + dr/dt_e)`` (stationary
  receiver, moving source, still air).
* ``t0`` in the straight-line model is the *emission* time of closest approach.
  The ridge is observed on the received axis, where CPA appears at ``t0 + d/c``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

DEFAULT_C = 343.0  # m/s, CLAUDE.md default


# ---------------------------------------------------------------------------
# Speed of sound
# ---------------------------------------------------------------------------
def speed_of_sound(temp_c: ArrayLike) -> NDArray | float:
    """Speed of sound in dry air (m/s) from temperature in Celsius: 331.3 + 0.606 T."""
    out = 331.3 + 0.606 * np.asarray(temp_c, dtype=float)
    return float(out) if out.ndim == 0 else out


# ---------------------------------------------------------------------------
# Straight, level pass (closed forms)
# ---------------------------------------------------------------------------
def straight_range(t_e: ArrayLike, v_mps: float, d_m: float, t0_s: float) -> NDArray:
    """r(t) = sqrt(d² + v²(t − t0)²) on the emission-time axis."""
    tau = np.asarray(t_e, dtype=float) - t0_s
    return np.sqrt(d_m**2 + (v_mps * tau) ** 2)


def straight_freq_emission(
    t_e: ArrayLike, f0_hz: float, v_mps: float, d_m: float, t0_s: float, c: float = DEFAULT_C
) -> NDArray:
    """Observed frequency f(t_e) = f0·c/(c + dr/dt) parameterised by emission time."""
    tau = np.asarray(t_e, dtype=float) - t0_s
    r = np.sqrt(d_m**2 + (v_mps * tau) ** 2)
    rdot = v_mps**2 * tau / r
    return f0_hz * c / (c + rdot)


def straight_emission_time(
    t_r: ArrayLike, v_mps: float, d_m: float, t0_s: float, c: float = DEFAULT_C
) -> NDArray:
    """Invert t_r = t_e + r(t_e)/c for a straight pass (exact, requires v < c).

    With s = t_r − t0 and τ = t_e − t0:
        τ = [c²s − sqrt(c²v²s² + d²(c² − v²))] / (c² − v²)
    """
    if v_mps >= c:
        raise ValueError("supersonic source: v must be < c")
    s = np.asarray(t_r, dtype=float) - t0_s
    c2, v2 = c * c, v_mps * v_mps
    tau = (c2 * s - np.sqrt(c2 * v2 * s * s + d_m * d_m * (c2 - v2))) / (c2 - v2)
    return t0_s + tau


def straight_freq_received(
    t_r: ArrayLike, f0_hz: float, v_mps: float, d_m: float, t0_s: float, c: float = DEFAULT_C
) -> NDArray:
    """Observed frequency as a function of *received* time (what a spectrogram shows)."""
    t_e = straight_emission_time(t_r, v_mps, d_m, t0_s, c)
    return straight_freq_emission(t_e, f0_hz, v_mps, d_m, t0_s, c)


def straight_asymptotes(f0_hz: float, v_mps: float, c: float = DEFAULT_C) -> tuple[float, float]:
    """(f_pre, f_post): approach and recede plateau frequencies."""
    return f0_hz * c / (c - v_mps), f0_hz * c / (c + v_mps)


def straight_max_rate(f0_hz: float, v_mps: float, d_m: float, c: float = DEFAULT_C) -> float:
    """|df/dt| at CPA (Hz/s): f0·v²/(c·d)."""
    return f0_hz * v_mps**2 / (c * d_m)


# ---------------------------------------------------------------------------
# Closed-form inversions (seeding / pruning only)
# ---------------------------------------------------------------------------
def invert_speed(f_pre_hz: float, f_post_hz: float, c: float = DEFAULT_C) -> float:
    """v = c·(f_pre − f_post)/(f_pre + f_post)."""
    return c * (f_pre_hz - f_post_hz) / (f_pre_hz + f_post_hz)


def invert_f0(f_pre_hz: float, f_post_hz: float) -> float:
    """f0 = 2·f_pre·f_post/(f_pre + f_post) (harmonic mean)."""
    return 2.0 * f_pre_hz * f_post_hz / (f_pre_hz + f_post_hz)


def invert_distance(f0_hz: float, v_mps: float, max_rate_hz_per_s: float, c: float = DEFAULT_C) -> float:
    """d = f0·v²/(c·maxRoC)."""
    return f0_hz * v_mps**2 / (c * abs(max_rate_hz_per_s))


def horizontal_offset(d_m: float, height_above_mic_m: float) -> float:
    """h = sqrt(d² − Δz²); 0 if the aircraft altitude already exceeds d (inconsistent)."""
    return float(np.sqrt(max(d_m**2 - height_above_mic_m**2, 0.0)))


# ---------------------------------------------------------------------------
# General track geometry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PredictedCurve:
    """Predicted Doppler curve for one track and one (or many) mic positions.

    Arrays have shape ``(..., N)``: leading dims follow the mic array, N follows
    the track samples. ``t_received_s`` is monotonic in N whenever |dr/dt| < c.
    """

    t_emission_s: NDArray   # (N,)
    t_received_s: NDArray   # (..., N)
    ratio: NDArray          # (..., N) observed / source frequency
    range_m: NDArray        # (..., N)
    range_rate_mps: NDArray # (..., N)


def track_velocity(track_t_s: NDArray, track_enu: NDArray) -> NDArray:
    """Finite-difference velocity (N, 3) of a sampled track (non-uniform times OK)."""
    return np.gradient(np.asarray(track_enu, float), np.asarray(track_t_s, float), axis=0)


def predicted_curve(
    track_t_s: ArrayLike,
    track_enu: ArrayLike,
    mic_enu: ArrayLike,
    c: float = DEFAULT_C,
    track_vel_enu: ArrayLike | None = None,
) -> PredictedCurve:
    """Predicted Doppler curve on the track's own emission samples.

    Parameters
    ----------
    track_t_s : (N,) emission times (epoch seconds, or any consistent axis).
    track_enu : (N, 3) aircraft positions in a local metric frame (m).
    mic_enu : (3,) or (..., 3) candidate microphone position(s), same frame.
    c : speed of sound (m/s).
    track_vel_enu : optional (N, 3) velocity; finite differences if omitted.

    Returns a :class:`PredictedCurve` where each sample i maps emission time
    ``t_e[i]`` to received time ``t_e[i] + r_i/c`` and Doppler ratio
    ``c/(c + dr/dt)``, with ``dr/dt = (A − P)·V / r``.
    """
    t = np.asarray(track_t_s, float)
    A = np.asarray(track_enu, float)
    V = track_velocity(t, A) if track_vel_enu is None else np.asarray(track_vel_enu, float)
    P = np.asarray(mic_enu, float)[..., None, :]          # (..., 1, 3)
    rel = A - P                                           # (..., N, 3)
    r = np.linalg.norm(rel, axis=-1)                      # (..., N)
    rdot = np.einsum("...nk,nk->...n", rel, V) / r
    return PredictedCurve(
        t_emission_s=t,
        t_received_s=t + r / c,
        ratio=c / (c + rdot),
        range_m=r,
        range_rate_mps=rdot,
    )


def _interp_track(t_q: NDArray, t: NDArray, X: NDArray) -> NDArray:
    """Linear interpolation of (N, 3) samples at arbitrary-shaped query times -> (..., 3)."""
    flat = t_q.reshape(-1)
    out = np.stack([np.interp(flat, t, X[:, k]) for k in range(X.shape[1])], axis=-1)
    return out.reshape(t_q.shape + (X.shape[1],))


def emission_times(
    t_received_s: ArrayLike,
    track_t_s: ArrayLike,
    track_enu: ArrayLike,
    mic_enu: ArrayLike,
    c: float = DEFAULT_C,
    track_vel_enu: ArrayLike | None = None,
    n_iter: int = 4,
) -> NDArray:
    """Solve t_r = t_e + |A(t_e) − P|/c for t_e by Newton iteration.

    ``t_received_s`` has shape (M,) and ``mic_enu`` shape (..., 3); the result
    broadcasts to (..., M). This is the scorer's workhorse: observed ridge
    times are fixed, so no per-candidate re-interpolation onto a received grid
    is needed. Converges quadratically because |dr/dt|/c < 1.
    """
    t = np.asarray(track_t_s, float)
    A = np.asarray(track_enu, float)
    V = track_velocity(t, A) if track_vel_enu is None else np.asarray(track_vel_enu, float)
    P = np.asarray(mic_enu, float)
    tr = np.asarray(t_received_s, float)
    shape = np.broadcast_shapes(P.shape[:-1] + (1,), (tr.size,))
    tr_b = np.broadcast_to(tr, shape)
    P_b = P[..., None, :]
    # Initial guess: delay evaluated at the received time.
    rel = _interp_track(tr_b, t, A) - P_b
    te = tr_b - np.linalg.norm(rel, axis=-1) / c
    for _ in range(n_iter):
        rel = _interp_track(te, t, A) - P_b
        r = np.linalg.norm(rel, axis=-1)
        rdot = np.einsum("...k,...k->...", rel, _interp_track(te, t, V)) / r
        g = te + r / c - tr_b
        te = te - g / (1.0 + rdot / c)
    return te


def predicted_ratio_at(
    t_received_s: ArrayLike,
    track_t_s: ArrayLike,
    track_enu: ArrayLike,
    mic_enu: ArrayLike,
    c: float = DEFAULT_C,
    track_vel_enu: ArrayLike | None = None,
) -> tuple[NDArray, NDArray, NDArray]:
    """Doppler ratio at given received times for one track and mic position(s).

    Returns ``(ratio, t_emission, range_m)`` each shaped (..., M). Received
    times outside the track's coverage are clamped by ``np.interp`` — callers
    should mask them using ``t_emission`` against the track's time span.
    """
    t = np.asarray(track_t_s, float)
    A = np.asarray(track_enu, float)
    V = track_velocity(t, A) if track_vel_enu is None else np.asarray(track_vel_enu, float)
    te = emission_times(t_received_s, t, A, mic_enu, c, V)
    P_b = np.asarray(mic_enu, float)[..., None, :]
    rel = _interp_track(te, t, A) - P_b
    r = np.linalg.norm(rel, axis=-1)
    rdot = np.einsum("...k,...k->...", rel, _interp_track(te, t, V)) / r
    return c / (c + rdot), te, r
