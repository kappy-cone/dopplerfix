"""Full curve modeling (step 7) and locked N-event scoring (step 8, milestone 5).

Interface fixed in milestone 4:
  * :func:`modeled_ratio` — implemented: the general track-based model on the
    received-time axis (emission-time radial velocity, propagation delay).
  * :func:`score_hypotheses` — milestone 5. Per hypothesis: one Δt shared by
    every event (bounded by the hypothesis' ``dt_bounds_s``) and one f0 per
    event, profiled out in closed form (f0 = Σ w·f·ρ / Σ w·ρ²) at each Δt.
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
    dt_halfwidth_s: float = 10.0
    dt_coarse_step_s: float = 0.5
    ridge_decimate_s: float = 0.5
    top_n: int = 10
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
    cost: float
    per_event_cost: NDArray   # (N,)
    n_points: NDArray         # (N,)


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


def score_hypotheses(events: Sequence[ObservedEvent], hyps: Sequence[Hypothesis], ts: TrackSet,
                     cell_xyz: dict[int, NDArray], start_time_s: float, c: float,
                     p: ScoreParams) -> list[CellScore]:
    """Locked N-event scoring (milestone 5)."""
    raise NotImplementedError("locked N-event scoring is milestone 5")
