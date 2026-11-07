"""Time-shift null tests: a report-only diagnostic of chance coincidence.

Every event is moved against the ADS-B data by a fixed fraction of the clip, wrapped around the clip, and the
cascade and locked scoring run again. If a *wrong* timing fits about as well as the real one, the real match
may be a coincidence. The idea comes from the method description of an independent tool with the same goal
(Espaye/aircraft-doppler, docs/method.md §5); this is a fresh implementation.

Report only: nothing here changes ranking, status labels or any other output of the real search, and no
pass/fail threshold is applied (the numbers go to run.json).

Shifting: an event moves rigidly. Its received CPA time becomes (t_rx + s) mod duration, and its ridge points
move by the same amount, so the curve keeps its shape. An event pushed past the end re-enters at the start.
Its v, d and covariance are unchanged (a time shift does not alter them).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np

from .cascade import STAGES, CascadeParams, DtPrior, Hypothesis, ObservedEvent, iter_cascade
from .geo import DEMSource, GridSpec, ecef_to_geodetic
from .score import ScoreParams, rank_cells
from .tracks import TrackSet


@dataclass(frozen=True)
class DiagnosticsParams:
    """config.yaml ``diagnostics:``. Both diagnostics are off by default and are report-only."""

    null_test: bool = False                                   # run the time-shift null tests
    null_shifts_frac: tuple[float, ...] = (0.25, 0.5, 0.75)   # shifts as fractions of the clip duration
    commit_result: bool = False                               # record a SHA-256 commitment of the top candidates
    commit_top_k: int = 5                                     # candidates included in the commitment

    @classmethod
    def from_dict(cls, d: dict) -> "DiagnosticsParams":
        kw = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        if "null_shifts_frac" in kw:
            kw["null_shifts_frac"] = tuple(float(x) for x in kw["null_shifts_frac"])
        return cls(**kw)


def shift_seconds(fracs: Sequence[float], duration_s: float) -> list[float]:
    """Shifts in seconds for fractions of the clip; each fraction must lie strictly inside (0, 1)."""
    out = []
    for f in fracs:
        if not 0.0 < float(f) < 1.0:
            raise ValueError(f"null-test shift fraction {f} must lie strictly between 0 and 1")
        out.append(float(f) * float(duration_s))
    return out


def wrap_time(t_s: float, shift_s: float, duration_s: float) -> float:
    """(t + shift) wrapped into [0, duration)."""
    return float(np.mod(float(t_s) + float(shift_s), float(duration_s)))


def shift_events(events: Sequence[ObservedEvent], shift_s: float, duration_s: float) -> list[ObservedEvent]:
    """Events moved by ``shift_s`` against the tracks, each wrapped as a unit (see the module docstring)."""
    out = []
    for e in events:
        t_new = wrap_time(e.t_rx_s, shift_s, duration_s)
        out.append(replace(e, t_rx_s=t_new, ridge_t_s=np.asarray(e.ridge_t_s, float) + (t_new - e.t_rx_s)))
    return out


def ridge_span_s(events: Sequence[ObservedEvent]) -> tuple[float, float]:
    """(earliest, latest) recording-relative ridge time over all events (t_rx if an event has no ridge)."""
    lo = min((float(np.min(e.ridge_t_s)) if len(e.ridge_t_s) else e.t_rx_s) for e in events)
    hi = max((float(np.max(e.ridge_t_s)) if len(e.ridge_t_s) else e.t_rx_s) for e in events)
    return lo, hi


@dataclass
class SearchBest:
    """Rank-1 outcome of one search (real or shifted), in the terms the ranking uses."""

    n_events: int
    n_candidates: int
    n_explained: int | None = None      # events the rank-1 hypothesis assigns a track to
    score: float | None = None          # rank-1 score (lower is better)
    dt_s: float | None = None
    lat: float | None = None
    lon: float | None = None
    tracks: list[str] = field(default_factory=list)   # icao24 per event, "" when unmatched
    survivors: dict = field(default_factory=dict)     # cpa / offset / silence cell counts

    @property
    def rank_key(self) -> tuple[float, float]:
        """Same order as the ranking: more events explained first, then the lower score. No candidate is worst."""
        if not self.n_candidates or self.n_explained is None:
            return (np.inf, np.inf)
        return (float(self.n_events - self.n_explained), float(self.score))

    def as_dict(self) -> dict:
        return {"n_events": self.n_events, "n_candidates": self.n_candidates, "n_explained": self.n_explained,
                "best_score": self.score, "dt_s": self.dt_s, "lat": self.lat, "lon": self.lon,
                "tracks": list(self.tracks), "survivors": dict(self.survivors)}


def search(spec: GridSpec, events: Sequence[ObservedEvent], ts: TrackSet, prior: DtPrior, dem_source: DEMSource,
           mic_height_m: float, c: float, cp: CascadeParams, sp: ScoreParams) -> SearchBest:
    """Cascade (active tiles) + locked scoring, reduced to the rank-1 outcome.

    Builds the scorer's cell list the way ``cli.run_locate`` does (silence survivors, sorted by interim cascade
    cost, every surviving hypothesis per cell), so an unshifted search reproduces the real rank 1
    (tests/test_nulltest.py checks this).
    """
    cells, counts = [], {k: 0 for k in STAGES if k != "grid"}
    for r in iter_cascade(spec, events, ts, prior, dem_source, mic_height_m, c, cp):
        tile = r.tile
        counts["cpa"] += int(r.cpa.sum())
        counts["offset"] += int(r.offset.sum())
        counts["silence"] += int(r.silence.sum())
        for h in np.flatnonzero(r.silence):
            cid = int(spec.cell_ids(tile.i[h], tile.j[h]))
            qs, pen = r.scoring_hypotheses(h)
            hyps = [Hypothesis(cid, tuple(int(k) for k in r.hypotheses.tracks[q]),
                               (float(r.hypotheses.dt_lo_s[q]), float(r.hypotheses.dt_hi_s[q])))
                    for q in qs]
            interim = float(r.hypotheses.cost[r.best[h]])
            cells.append((interim, (cid, tile.xyz[h].copy(), hyps, pen)))
    cells.sort(key=lambda x: x[0])              # stable, like run_locate's interim ranking
    out = SearchBest(n_events=len(events), n_candidates=len(cells), survivors=counts)
    if not cells:
        return out
    ranked = rank_cells(events, [cl for _, cl in cells], ts, prior.start_s, c, sp, (prior.dt_lo_s, prior.dt_hi_s),
                        0.5 * spec.spacing_m)
    best = min(ranked, key=lambda r: (sum(k < 0 for k in r.best.assignment), r.best.cost))
    la, lo, _ = ecef_to_geodetic(best.xyz_est)
    out.n_explained = int(sum(k >= 0 for k in best.best.assignment))
    out.score, out.dt_s = float(best.best.cost), float(best.best.dt_s)
    out.lat, out.lon = float(la), float(lo)
    out.tracks = [str(ts.icao24[k]) if k >= 0 else "" for k in best.best.assignment]
    return out


def summarize(real: SearchBest, shifted: Sequence[tuple[float, float, SearchBest]]) -> dict:
    """run.json ``null_test`` block: the real rank 1, each shifted rank 1, and how they compare.

    ``shifted``: (fraction, shift_s, SearchBest) per shift. Comparison is lexicographic, like the ranking:
    ``explained_gap`` = events explained by the real rank 1 minus by the best shifted one (> 0: the real timing
    explains more). ``best_shifted_over_real`` (score ratio) and ``score_gap`` (shifted − real) are given only
    when both explain the same number of events and, for the ratio, the real score is positive.
    ``n_shifted_not_worse`` counts shifts that have a candidate whose rank 1 ranks at or above the real one.
    """
    rows = [{"shift_frac": float(f), "shift_s": float(s), **b.as_dict()} for f, s, b in shifted]
    out = {"shifts_frac": [float(f) for f, _, _ in shifted], "real": real.as_dict(), "shifted": rows,
           "best_shifted": None, "explained_gap": None, "score_gap": None, "best_shifted_over_real": None,
           "n_shifted_not_worse": None}
    if not shifted:
        return out
    j = min(range(len(shifted)), key=lambda i: shifted[i][2].rank_key)
    b = shifted[j][2]
    out["best_shifted"] = j
    out["n_shifted_not_worse"] = int(sum(bool(s.n_candidates) and s.rank_key <= real.rank_key for _, _, s in shifted))
    if real.n_explained is not None and b.n_explained is not None:
        out["explained_gap"] = int(real.n_explained - b.n_explained)
        if out["explained_gap"] == 0:
            out["score_gap"] = float(b.score - real.score)
            if real.score > 0:
                out["best_shifted_over_real"] = float(b.score / real.score)
    return out
