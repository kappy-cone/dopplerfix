"""Cascade (methodology steps 3–6): grid tiles, CPA prefilter, inter-event offset filter, silence filter.

Everything is streamed tile by tile: :func:`iter_cascade` yields one
:class:`TileResult` per active tile, so the full grid never exists in memory
(or on disk). Spatial pruning uses cKDTrees over track samples near each
event's time; tiles with no samples in reach of *every* event are skipped
before their DEM window is even read.

Time model. The nominal recording start is ``S`` (known mode: the clip's
stated UTC start). The true start is ``S + Δt`` with Δt inside the prior
(:class:`DtPrior`). An event observed at recording-relative received CPA
time τ_e must be produced by a pass whose predicted received CPA time
T = t_cpa + r_cpa/c satisfies |T − S − τ_e − Δt| ≤ tol_t,e. Every event shares
the same Δt; relative timing comes only from track positions and propagation.
That makes the offset filter an interval intersection over events of the union
over candidate passes, for any number of events.
"""
from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass, field, fields
from typing import Iterator, Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import cKDTree

from . import aircraft_types as at
from .geo import DEMSource, GridSpec, GridTile, build_tile, tile_ecef_centres, tiles_to_geodetic
from .tracks import TrackSet

log = logging.getLogger(__name__)
STAGES = ("grid", "cpa", "offset", "silence")


# ---------------------------------------------------------------------------
# Parameters and events
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CascadeParams:
    n_sigma: float = 3.0
    truncated_coverage: float = 2.0      # coverage (in d/v) below this on a side -> widen σ
    truncated_tol_mult: float = 2.0      # σ multiplier per clipped side
    wind_sys_mps: float = 15.0           # PROVISIONAL: wind allowance on v (and via d = v·τ, on d)
    height_sys_m: float = 30.0           # PROVISIONAL: DEM + geoid + ADS-B geometric altitude error
    timing_sys_s: float = 1.0            # PROVISIONAL: straight-line t0 bias on curved/climbing tracks
    baro_alt_tol_m: float = 150.0        # extra height allowance for barometric-altitude tracks
    tile_cells: int = 128                # tile edge in cells
    window_pad_s: float = 2.0
    prune_height_margin_m: float = 5000.0  # tile pruning bound on ground height (not a tolerance)
    audible_slant_m: dict = field(default_factory=lambda: {"prop": 3000.0, "jet": 6000.0, "unknown": 6000.0})
    silence_mode: str = "reject"         # reject | count
    silence_max_unexplained: int = 0
    silence_min_audible_s: float = 20.0  # in-clip audible duration for a pass to count as expected
    silence_dt_s: float = 1.0            # track decimation for the silence tree
    explain_time_tol_s: float = 20.0
    silence_explain: str = "cpa"         # cpa: only CPA-consistent passes explain | time: any compatible event in time
    allow_shared_tracks: bool = False    # from score.allow_shared_tracks
    max_hypotheses_per_cell: int = 64    # from score.max_hypotheses_per_cell

    @classmethod
    def from_dict(cls, d: dict) -> "CascadeParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def threshold(self, cls_code: int) -> float:
        return float(self.audible_slant_m[at.CLASS_NAMES[int(cls_code)]])


@dataclass(frozen=True)
class ObservedEvent:
    """What the cascade and the scorer may use from a fitted event (never its t0 as a free parameter)."""

    idx: int
    cls: int
    t_rx_s: float                  # observed received CPA time, recording-relative
    v_mps: float
    d_m: float
    f0_hz: float                   # characterization fit (display / initial value only)
    cov: NDArray                   # (3, 3) covariance of (v, d, t_rx)
    coverage_tau: tuple[float, float]
    ridge_t_s: NDArray
    ridge_f_hz: NDArray
    ridge_w: NDArray

    @property
    def sigma(self) -> NDArray:
        return np.sqrt(np.clip(np.diag(self.cov), 0, None))


def observed_events(fits: Sequence, c: float) -> list[ObservedEvent]:
    """EventFit list -> ObservedEvent list. t_rx = t0 + d/c, with its variance propagated."""
    out = []
    for k, e in enumerate(fits):
        C = np.asarray(e.cov, float)[:4, :4]          # (f0, v, d, t0)
        J = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 1 / c, 1]], float)
        cov3 = J @ C @ J.T
        w = np.full(len(e.ridge.t_s), 1.0 / max(e.noise_hz if np.isfinite(e.noise_hz) else 1.0, 1e-3) ** 2)
        out.append(ObservedEvent(k, int(e.cls), float(e.t_cpa_received_s), float(e.v_mps), float(e.d_m),
                                 float(e.f0_hz), cov3, tuple(float(x) for x in e.coverage),
                                 np.asarray(e.ridge.t_s, float), np.asarray(e.ridge.f_hz, float), w))
    return out


@dataclass(frozen=True)
class Tolerances:
    """Per-event tolerances: √((n_σ·w_cov·σ)² + sys²), plus the hard grid-quantization bound added linearly."""

    v_mps: float
    d_core_m: float        # without the grid term and without the per-track baro allowance
    d_grid_m: float
    t_rx_s: float
    w_cov: float

    def d_for(self, baro: NDArray | bool, baro_tol_m: float) -> NDArray:
        return np.hypot(self.d_core_m, np.where(baro, baro_tol_m, 0.0)) + self.d_grid_m


def event_tolerances(ev: ObservedEvent, p: CascadeParams, c: float, cell_half_diag_m: float) -> Tolerances:
    """Tolerances from the fit covariance, widened for edge-clipped passes, with absolute physical allowances.

    sys terms: v ← wind; d ← height error ⊕ d·wind/v (wind biases v, and d = v·τ);
    t ← model error ⊕ d·wind/c² (propagation in wind). The grid term is a hard
    bound (the mic can be up to half a cell diagonal from the cell centre), so it
    is added linearly: δd ≤ h, δT ≤ h/v + h/c. Clock error is in the Δt prior.
    """
    sv, sd, st = (np.inf if not np.isfinite(s) else s for s in ev.sigma)
    n_clipped = sum(1 for x in ev.coverage_tau if x < p.truncated_coverage)
    w = p.truncated_tol_mult ** n_clipped
    k = p.n_sigma * w
    v = max(ev.v_mps, 1.0)
    h = cell_half_diag_m
    return Tolerances(
        v_mps=float(np.hypot(k * sv, p.wind_sys_mps)),
        d_core_m=float(np.hypot(k * sd, np.hypot(p.height_sys_m, ev.d_m * p.wind_sys_mps / v))),
        d_grid_m=float(h),
        t_rx_s=float(np.hypot(k * st, np.hypot(p.timing_sys_s, ev.d_m * p.wind_sys_mps / c ** 2)) + h / v + h / c),
        w_cov=float(w),
    )


@dataclass(frozen=True)
class DtPrior:
    """True recording start = start_s + Δt, Δt ∈ [dt_lo_s, dt_hi_s]."""

    start_s: float
    dt_lo_s: float
    dt_hi_s: float
    duration_s: float
    mode: str = "known"


def dt_prior_from(mode: str, start_s: float | None, window_s: tuple[float, float] | None, clock_tol_s: float,
                  duration_s: float, track_span_s: tuple[float, float] | None = None) -> DtPrior:
    """known: ±clock_tol about the stated start; window: the window; relative: anywhere tracks exist."""
    if mode == "known":
        return DtPrior(float(start_s), -clock_tol_s, clock_tol_s, duration_s, mode)
    if mode == "window":
        a, b = window_s
        return DtPrior(float(a), -clock_tol_s, float(b - a) + clock_tol_s, duration_s, mode)
    if track_span_s is None:
        raise ValueError("relative time mode needs the track time span")
    a, b = track_span_s
    return DtPrior(float(a), 0.0, max(0.0, float(b - a - duration_s)), duration_s, "relative")


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
@dataclass
class _Table:
    """Column-oriented table of equal-length numpy arrays."""

    def __len__(self) -> int:
        return len(getattr(self, fields(self)[0].name))

    def take(self, idx):
        return type(self)(**{f.name: getattr(self, f.name)[idx] for f in fields(self)})

    @classmethod
    def concat(cls, parts: list):
        if not parts:
            return cls.empty()
        return cls(**{f.name: np.concatenate([getattr(p, f.name) for p in parts]) for f in fields(cls)})


@dataclass
class PassTable(_Table):
    """One row per (cell, event, track pass) that survived the CPA prefilter."""

    cell: NDArray          # row index into the tile
    event: NDArray         # int16
    track: NDArray         # int32 TrackSet index
    t_emit_s: NDArray      # emission CPA time (epoch)
    r_cpa_m: NDArray
    t_rx_pred_s: NDArray   # t_emit + r/c (epoch)
    speed_mps: NDArray
    dt_center_s: NDArray   # T − S − τ_e: the Δt this pass alone implies
    tol_t_s: NDArray
    z_v: NDArray           # (speed − v)/tol_v
    z_d: NDArray           # (r − d)/tol_d
    pos: NDArray           # (P, 3) ECEF aircraft position at CPA

    @classmethod
    def empty(cls) -> "PassTable":
        z = np.zeros(0)
        return cls(np.zeros(0, np.int64), np.zeros(0, np.int16), np.zeros(0, np.int32), z, z, z, z, z, z, z, z,
                   np.zeros((0, 3)))


@dataclass
class HypothesisTable(_Table):
    """Candidate assignments (one pass per event) that satisfy the shared-Δt constraint."""

    cell: NDArray          # row index into the tile
    passes: NDArray        # (H, N) int64 indices into the tile's PassTable
    tracks: NDArray        # (H, N) int32 TrackSet indices
    dt_lo_s: NDArray
    dt_hi_s: NDArray
    cost: NDArray          # interim cascade-consistency: RMS over events of |z| (v, d, t at the Δt midpoint)

    @classmethod
    def empty(cls, n_events: int = 1) -> "HypothesisTable":
        z = np.zeros(0)
        return cls(np.zeros(0, np.int64), np.zeros((0, n_events), np.int64), np.zeros((0, n_events), np.int32),
                   z, z, z)


@dataclass(frozen=True)
class Hypothesis:
    """One scoring hypothesis: a cell, one track per event, and the allowed range of the shared Δt."""

    cell_id: int
    assignment: tuple[int, ...]           # TrackSet index per event
    dt_bounds_s: tuple[float, float]


# ---------------------------------------------------------------------------
# Spatial indexes over track samples
# ---------------------------------------------------------------------------
@dataclass
class EventIndex:
    """KD-tree over class-compatible track samples inside one event's emission window."""

    event: int
    tree: cKDTree | None
    track: NDArray
    sample: NDArray
    radius_m: float
    tol: Tolerances


def build_event_index(ev: ObservedEvent, tol: Tolerances, ts: TrackSet, prior: DtPrior, c: float,
                      p: CascadeParams) -> EventIndex:
    radius = ev.d_m + tol.d_for(True, p.baro_alt_tol_m).item()
    t = ts.t_s
    lo = prior.start_s + ev.t_rx_s + prior.dt_lo_s - tol.t_rx_s - radius / c - p.window_pad_s
    hi = prior.start_s + ev.t_rx_s + prior.dt_hi_s + tol.t_rx_s + p.window_pad_s
    tm = (t >= lo) & (t <= hi)
    km = at.compatible(ev.cls, ts.cls)
    valid = ts.valid()[np.ix_(km, tm)]
    kk, ss = np.nonzero(valid)
    track = np.flatnonzero(km)[kk].astype(np.int32)
    sample = np.flatnonzero(tm)[ss].astype(np.int64)
    tree = cKDTree(ts.xyz[track, sample]) if len(track) else None
    return EventIndex(ev.idx, tree, track, sample, float(radius), tol)


@dataclass
class SilenceIndex:
    """KD-tree over all track samples (decimated) whose sound could arrive during the clip."""

    tree: cKDTree | None
    track: NDArray
    sample: NDArray        # index into the decimated time axis
    t_s: NDArray           # emission time of each point
    thr_m: NDArray         # audibility threshold of each point's track class
    radius_m: float
    dt_s: float


def build_silence_index(ts: TrackSet, prior: DtPrior, c: float, p: CascadeParams) -> SilenceIndex:
    step = max(1, int(round(p.silence_dt_s / ts.dt_s)))
    thr_by_cls = np.array([p.threshold(k) for k in range(len(at.CLASS_NAMES))])
    rmax = float(thr_by_cls.max())
    t = ts.t_s[::step]
    lo = prior.start_s + prior.dt_lo_s - rmax / c - p.window_pad_s
    hi = prior.start_s + prior.dt_hi_s + prior.duration_s + p.window_pad_s
    tm = np.flatnonzero((t >= lo) & (t <= hi))
    valid = ts.valid()[:, ::step][:, tm]
    kk, ss = np.nonzero(valid)
    xyz = ts.xyz[:, ::step][kk, tm[ss]]
    return SilenceIndex(cKDTree(xyz) if len(kk) else None, kk.astype(np.int32), tm[ss].astype(np.int64),
                        t[tm[ss]], thr_by_cls[ts.cls[kk]], rmax, step * ts.dt_s)


# ---------------------------------------------------------------------------
# Stage 4: CPA prefilter
# ---------------------------------------------------------------------------
def _runs(keys: tuple[NDArray, ...], sample: NDArray) -> NDArray:
    """Run id for rows already sorted by (keys..., sample): new run when a key changes or samples are not adjacent."""
    n = len(sample)
    brk = np.ones(n, bool)
    if n > 1:
        b = np.diff(sample) != 1
        for k in keys:
            b |= np.diff(k) != 0
        brk[1:] = b
    return np.cumsum(brk) - 1


def _refine_cpa(P: NDArray, ts: TrackSet, k: NDArray, s: NDArray) -> tuple[NDArray, NDArray, NDArray, NDArray]:
    """Closest approach of points P to track k's linear-interpolated polyline around sample s (s−1..s+1).

    Returns (t_emit, r, pos, vel). Linear interpolation matches TrackSet.sample.
    """
    T = ts.xyz.shape[1]
    best_r = np.linalg.norm(ts.xyz[k, s] - P, axis=1)
    best_u = np.zeros(len(s))
    best_s0 = s.astype(np.int64).copy()
    for d in (-1, 0):                         # segments [s−1, s] and [s, s+1]
        s0 = s + d
        ok = (s0 >= 0) & (s0 + 1 < T)
        s0c = np.clip(s0, 0, T - 2)
        A, B = ts.xyz[k, s0c], ts.xyz[k, s0c + 1]
        ok &= np.isfinite(A[:, 0]) & np.isfinite(B[:, 0])
        AB = B - A
        L2 = np.einsum("ij,ij->i", AB, AB)
        u = np.clip(np.einsum("ij,ij->i", P - A, AB) / np.where(L2 > 0, L2, 1.0), 0.0, 1.0)
        r = np.linalg.norm(A + u[:, None] * AB - P, axis=1)
        better = ok & (r < best_r)
        best_r = np.where(better, r, best_r)
        best_u = np.where(better, u, best_u)
        best_s0 = np.where(better, s0c, best_s0)
    s1 = np.minimum(best_s0 + 1, T - 1)
    w = best_u[:, None]
    pos = (1 - w) * ts.xyz[k, best_s0] + w * ts.xyz[k, s1]
    v1 = np.where(np.isfinite(ts.vel[k, s1]), ts.vel[k, s1], ts.vel[k, best_s0])
    vel = (1 - w) * ts.vel[k, best_s0] + w * v1
    t_emit = ts.t0_s + ts.dt_s * (best_s0 + best_u)
    return t_emit, best_r, pos, vel


def cpa_prefilter(tile: GridTile, events: Sequence[ObservedEvent], indexes: Sequence[EventIndex], ts: TrackSet,
                  prior: DtPrior, c: float, p: CascadeParams) -> PassTable:
    """Per (cell, event, class-compatible track) closest approach; keep passes consistent with v, d and time."""
    parts = []
    if len(tile) == 0:
        return PassTable.empty()
    cell_tree = cKDTree(tile.xyz)
    baro = ts.alt_source == "baro"
    for ev, ix in zip(events, indexes):
        if ix.tree is None:
            continue
        m = cell_tree.sparse_distance_matrix(ix.tree, ix.radius_m, output_type="ndarray")
        if len(m) == 0:
            continue
        cell, pt = m["i"].astype(np.int64), m["j"].astype(np.int64)
        trk, smp, dist = ix.track[pt], ix.sample[pt], m["v"]
        o = np.lexsort((smp, trk, cell))
        cell, trk, smp, dist = cell[o], trk[o], smp[o], dist[o]
        run = _runs((cell, trk), smp)
        o2 = np.lexsort((dist, run))           # first row of each run = its minimum
        first = o2[np.r_[True, np.diff(run[o2]) != 0]]
        cell, trk, smp = cell[first], trk[first], smp[first]
        t_emit, r, pos, vel = _refine_cpa(tile.xyz[cell], ts, trk, smp)
        speed = np.linalg.norm(vel, axis=1)
        t_rx = t_emit + r / c
        dtc = t_rx - prior.start_s - ev.t_rx_s
        tol = ix.tol
        tol_d = tol.d_for(baro[trk], p.baro_alt_tol_m)
        z_v = (speed - ev.v_mps) / tol.v_mps
        z_d = (r - ev.d_m) / tol_d
        ok = (np.abs(z_v) <= 1) & (np.abs(z_d) <= 1)
        ok &= (dtc + tol.t_rx_s >= prior.dt_lo_s) & (dtc - tol.t_rx_s <= prior.dt_hi_s)
        ok &= np.isfinite(speed)
        if not ok.any():
            continue
        n = int(ok.sum())
        parts.append(PassTable(cell[ok], np.full(n, ev.idx, np.int16), trk[ok].astype(np.int32), t_emit[ok], r[ok],
                               t_rx[ok], speed[ok], dtc[ok], np.full(n, tol.t_rx_s), z_v[ok], z_d[ok], pos[ok]))
    return PassTable.concat(parts)


def cells_with_all_events(passes: PassTable, n_cells: int, n_events: int) -> NDArray:
    """(M,) True where every event has at least one surviving pass."""
    have = np.zeros((n_cells, n_events), bool)
    have[passes.cell, passes.event] = True
    return have.all(axis=1)


# ---------------------------------------------------------------------------
# Stage 5: inter-event offset filter (shared Δt) + hypotheses
# ---------------------------------------------------------------------------
def offset_filter(passes: PassTable, n_cells: int, n_events: int, track_icao: NDArray, prior: DtPrior,
                  p: CascadeParams) -> tuple[NDArray, HypothesisTable]:
    """Cells where one Δt (inside the prior) is consistent with a pass for every event.

    Feasible Δt set per cell = ∩_events ∪_passes [dtc − tol, dtc + tol] ∩ prior. If it is
    non-empty it contains some pass's left end, so only those points are tested
    (vectorized). Hypotheses (one pass per event, distinct aircraft unless
    ``allow_shared_tracks``) are then enumerated for surviving cells only.
    Returns (keep mask over cells, hypotheses).
    """
    keep = np.zeros(n_cells, bool)
    if len(passes) == 0:
        return keep, HypothesisTable.empty(n_events)
    a = np.maximum(passes.dt_center_s - passes.tol_t_s, prior.dt_lo_s)
    b = np.minimum(passes.dt_center_s + passes.tol_t_s, prior.dt_hi_s)
    o = np.argsort(passes.cell, kind="stable")
    cells_sorted = passes.cell[o]
    starts = np.searchsorted(cells_sorted, np.arange(n_cells), "left")
    ends = np.searchsorted(cells_sorted, np.arange(n_cells), "right")
    cnt = ends - starts
    # candidate q (each pass's left end) × every pass of the same cell
    q_cnt = cnt[passes.cell]
    q = np.repeat(np.arange(len(passes)), q_cnt)
    offs = np.arange(len(q)) - np.repeat(np.cumsum(q_cnt) - q_cnt, q_cnt)
    pp = o[starts[passes.cell[q]] + offs]
    x = a[q]
    inside = (a[pp] <= x + 1e-9) & (x <= b[pp] + 1e-9)
    covered = np.zeros((len(passes), n_events), bool)
    covered[q[inside], passes.event[pp[inside]]] = True
    feasible_q = covered.all(axis=1) & (a <= b)
    keep[np.unique(passes.cell[feasible_q])] = True

    # Hypotheses for surviving cells (small sets: Python is fine here).
    rows = []
    qi, pi = q[inside], pp[inside]
    fe = feasible_q[qi]
    qi, pi = qi[fe], pi[fe]
    by_q: dict[int, list[int]] = {}
    for qq, p_ in zip(qi.tolist(), pi.tolist()):
        by_q.setdefault(qq, []).append(p_)
    seen: dict[int, set] = {}
    for qq, plist in by_q.items():
        cell = int(passes.cell[qq])
        per_ev = [[p_ for p_ in plist if passes.event[p_] == e] for e in range(n_events)]
        s = seen.setdefault(cell, set())
        if len(s) >= p.max_hypotheses_per_cell:
            continue
        for combo in itertools.product(*per_ev):
            if combo in s:
                continue
            if not p.allow_shared_tracks:
                ids = [track_icao[passes.track[c_]] for c_ in combo]
                if len(set(ids)) < len(ids):
                    continue
            lo, hi = float(np.max(a[list(combo)])), float(np.min(b[list(combo)]))
            if lo > hi + 1e-9:
                continue
            s.add(combo)
            mid = 0.5 * (lo + hi)
            cidx = list(combo)
            zt = (passes.dt_center_s[cidx] - mid) / passes.tol_t_s[cidx]
            z2 = zt ** 2 + passes.z_v[cidx] ** 2 + passes.z_d[cidx] ** 2
            rows.append((cell, combo, lo, hi, float(np.sqrt(np.mean(z2)))))
            if len(s) >= p.max_hypotheses_per_cell:
                break
    if not rows:
        return np.zeros(n_cells, bool), HypothesisTable.empty(n_events)
    hcell = np.array([r[0] for r in rows], np.int64)
    hp = np.array([r[1] for r in rows], np.int64).reshape(len(rows), n_events)
    hyp = HypothesisTable(hcell, hp, passes.track[hp].astype(np.int32), np.array([r[2] for r in rows]),
                          np.array([r[3] for r in rows]), np.array([r[4] for r in rows]))
    keep = np.zeros(n_cells, bool)
    keep[hcell] = True
    return keep, hyp


def best_hypothesis(hyp: HypothesisTable, n_cells: int) -> NDArray:
    """(M,) index of the lowest-cost hypothesis per cell (−1 if none)."""
    best = np.full(n_cells, -1, np.int64)
    if len(hyp) == 0:
        return best
    o = np.lexsort((hyp.cost, hyp.cell))
    first = o[np.r_[True, np.diff(hyp.cell[o]) != 0]]
    best[hyp.cell[first]] = first
    return best


# ---------------------------------------------------------------------------
# Stage 6: silence filter
# ---------------------------------------------------------------------------
@dataclass
class SilenceResult:
    keep: NDArray              # (M,) over the cells passed in
    n_unexplained: NDArray     # (M,) int
    n_audible: NDArray         # (M,) int
    culprit_cell: NDArray      # rows: (cell, track) of unexplained audible passes
    culprit_track: NDArray


def silence_filter(cell_xyz: NDArray, dt_s: NDArray, events: Sequence[ObservedEvent], ix: SilenceIndex,
                   track_cls: NDArray, prior: DtPrior, c: float, p: CascadeParams,
                   explainers: tuple[NDArray, NDArray, NDArray] | None = None) -> SilenceResult:
    """Reject cells where some track would be audible during the clip with no matching event.

    A pass is audible when its slant range is below its class threshold for at
    least ``silence_min_audible_s`` of received time inside the clip (at that
    cell's Δt). Explained, with ``silence_explain``:
      * ``cpa`` (default): the pass is one of this cell's CPA-consistent passes for
        some event — ``explainers`` = (cell row, track, event) of those passes — and
        its in-clip closest approach is within ``explain_time_tol_s`` of that event.
        Another aircraft overflying at the same time as an event is NOT explained.
      * ``time``: any class-compatible event within ``explain_time_tol_s`` (lenient).
    """
    M = len(cell_xyz)
    n_un = np.zeros(M, np.int64)
    n_aud = np.zeros(M, np.int64)
    empty = np.zeros(0, np.int64)
    if M == 0 or ix.tree is None:
        return SilenceResult(np.ones(M, bool), n_un, n_aud, empty, empty)
    m = cKDTree(cell_xyz).sparse_distance_matrix(ix.tree, ix.radius_m, output_type="ndarray")
    cell, pt, r = m["i"].astype(np.int64), m["j"].astype(np.int64), m["v"]
    ok = r < ix.thr_m[pt]
    cell, pt, r = cell[ok], pt[ok], r[ok]
    tau = ix.t_s[pt] + r / c - prior.start_s - dt_s[cell]          # recording-relative received time
    ok = (tau >= 0) & (tau <= prior.duration_s)
    cell, pt, r, tau = cell[ok], pt[ok], r[ok], tau[ok]
    if len(cell) == 0:
        return SilenceResult(np.ones(M, bool), n_un, n_aud, empty, empty)
    trk, smp = ix.track[pt], ix.sample[pt]
    o = np.lexsort((smp, trk, cell))
    cell, trk, smp, r, tau = cell[o], trk[o], smp[o], r[o], tau[o]
    run = _runs((cell, trk), smp)
    nrun = run[-1] + 1
    dur = np.bincount(run, minlength=nrun) * ix.dt_s
    o2 = np.lexsort((r, run))
    first = o2[np.r_[True, np.diff(run[o2]) != 0]]
    rc, rt, rtau = cell[first], trk[first], tau[first]
    audible = dur >= p.silence_min_audible_s
    ev_t = np.array([e.t_rx_s for e in events])
    ev_c = np.array([e.cls for e in events])
    expl = np.zeros(nrun, bool)
    if p.silence_explain == "time" or explainers is None:
        for t_e, c_e in zip(ev_t, ev_c):
            expl |= at.compatible(c_e, track_cls[rt]) & (np.abs(rtau - t_e) <= p.explain_time_tol_s)
    elif p.silence_explain == "cpa":
        xc, xt, xe = (np.asarray(a, np.int64) for a in explainers)
        K = int(max(track_cls.size, 1))
        key_x = xc * K + xt
        o = np.argsort(key_x, kind="stable")
        key_x, xe = key_x[o], xe[o]
        key_r = rc * K + rt
        lo, hi = np.searchsorted(key_x, key_r, "left"), np.searchsorted(key_x, key_r, "right")
        for r_ in np.flatnonzero(hi > lo):      # few: only runs of tracks that passed the CPA prefilter here
            expl[r_] = np.any(np.abs(rtau[r_] - ev_t[xe[lo[r_]:hi[r_]]]) <= p.explain_time_tol_s)
    else:
        raise ValueError(f"silence_explain must be cpa or time, not {p.silence_explain!r}")
    un = audible & ~expl
    np.add.at(n_aud, rc[audible], 1)
    np.add.at(n_un, rc[un], 1)
    keep = np.ones(M, bool) if p.silence_mode == "count" else n_un <= p.silence_max_unexplained
    return SilenceResult(keep, n_un, n_aud, rc[un], rt[un].astype(np.int64))


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
@dataclass
class TileResult:
    """One tile's outcome. Arrays over ``tile`` cells; passes/hypotheses refer to tile row indices."""

    tile: GridTile
    cpa: NDArray
    offset: NDArray
    silence: NDArray
    passes: PassTable
    hypotheses: HypothesisTable
    best: NDArray                  # (M,) best hypothesis index or −1
    dt_s: NDArray                  # (M,) Δt used for silence (best hypothesis midpoint; NaN if none)
    n_unexplained: NDArray
    n_audible: NDArray
    culprit_track: NDArray         # TrackSet indices of unexplained audible passes at cells that reached silence
    seconds: dict


@dataclass
class CascadeSetup:
    events: list[ObservedEvent]
    tolerances: list[Tolerances]
    indexes: list[EventIndex]
    silence_index: SilenceIndex
    tile_origins: NDArray           # active tiles only
    n_tiles_total: int


def prepare_cascade(spec: GridSpec, events: Sequence[ObservedEvent], ts: TrackSet, prior: DtPrior, c: float,
                    p: CascadeParams) -> CascadeSetup:
    """Tolerances, KD-trees, and the active tiles (in reach of track samples for every event)."""
    tols = [event_tolerances(e, p, c, spec.cell_half_diag_m) for e in events]
    idx = [build_event_index(e, t, ts, prior, c, p) for e, t in zip(events, tols)]
    sil = build_silence_index(ts, prior, c, p)
    origins = spec.tile_origins()
    n_total = len(origins)
    if len(origins) and events:
        centres = tile_ecef_centres(spec, origins, 0.0)
        active = np.ones(len(origins), bool)
        for ix in idx:
            if ix.tree is None:
                active[:] = False
                break
            rr = ix.radius_m + spec.tile_half_diag_m + p.prune_height_margin_m
            active &= ix.tree.query_ball_point(centres, rr, return_length=True) > 0
        origins = origins[active]
    return CascadeSetup(list(events), tols, idx, sil, origins, n_total)


def run_tile(spec: GridSpec, i0: int, j0: int, setup: CascadeSetup, ts: TrackSet, prior: DtPrior,
             dem_source: DEMSource, mic_height_m: float, c: float, p: CascadeParams, to_geo=None) -> TileResult:
    sec = {}
    t0 = time.perf_counter()
    tile = build_tile(spec, i0, j0, dem_source, mic_height_m, to_geo)
    sec["grid"] = time.perf_counter() - t0
    M, N = len(tile), len(setup.events)
    t0 = time.perf_counter()
    passes = cpa_prefilter(tile, setup.events, setup.indexes, ts, prior, c, p)
    cpa = cells_with_all_events(passes, M, N) if N else np.zeros(M, bool)
    passes = passes.take(np.flatnonzero(cpa[passes.cell])) if len(passes) else passes
    sec["cpa"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    offset, hyp = offset_filter(passes, M, N, ts.icao24, prior, p)
    best = best_hypothesis(hyp, M)
    dt = np.full(M, np.nan)
    has = best >= 0
    dt[has] = 0.5 * (hyp.dt_lo_s[best[has]] + hyp.dt_hi_s[best[has]])
    sec["offset"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    silence = np.zeros(M, bool)
    n_un = np.zeros(M, np.int64)
    n_aud = np.zeros(M, np.int64)
    culprits = np.zeros(0, np.int64)
    rows = np.flatnonzero(offset)
    if len(rows):
        pos = np.full(M, -1, np.int64)
        pos[rows] = np.arange(len(rows))
        on = pos[passes.cell] >= 0
        expl = (pos[passes.cell[on]], passes.track[on], passes.event[on])
        sr = silence_filter(tile.xyz[rows], dt[rows], setup.events, setup.silence_index, ts.cls, prior, c, p, expl)
        silence[rows] = sr.keep
        n_un[rows], n_aud[rows] = sr.n_unexplained, sr.n_audible
        culprits = np.unique(sr.culprit_track)
    sec["silence"] = time.perf_counter() - t0
    return TileResult(tile, cpa, offset, silence, passes, hyp, best, dt, n_un, n_aud, culprits, sec)


def iter_cascade(spec: GridSpec, events: Sequence[ObservedEvent], ts: TrackSet, prior: DtPrior,
                 dem_source: DEMSource, mic_height_m: float, c: float, p: CascadeParams,
                 setup: CascadeSetup | None = None, all_tiles: bool = False) -> Iterator[TileResult]:
    """Yield one TileResult per tile (active tiles only, unless ``all_tiles`` for writing a full grid layer)."""
    setup = setup or prepare_cascade(spec, events, ts, prior, c, p)
    to_geo = tiles_to_geodetic(spec)
    origins = spec.tile_origins() if all_tiles else setup.tile_origins
    for i0, j0 in origins:
        yield run_tile(spec, int(i0), int(j0), setup, ts, prior, dem_source, mic_height_m, c, p, to_geo)
