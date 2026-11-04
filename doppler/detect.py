"""Flyover event detection on a spectrogram.

Statistic: per frame, the maximum over the band of the excess above each
frequency bin's median (a tonality measure that responds to the Doppler ridge
and to broadband rise alike), running-median smoothed. Windows are triggered
at ``baseline + trigger_db`` and extended (hysteresis) while above
``baseline + extend_db``, where the baseline is a low percentile of the
statistic so it self-calibrates to the recording. Merged flyovers are split at
deep dips. The descending-ridge confirmation happens in fit.characterize_events.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import median_filter
from scipy.signal import find_peaks

from .audio import Spectrogram


@dataclass(frozen=True)
class DetectParams:
    band_hz: tuple[float, float] = (40.0, 900.0)
    smooth_s: float = 3.0
    trigger_db: float = 10.0       # above baseline
    extend_db: float = 4.0         # above baseline
    baseline_percentile: float = 20.0
    min_duration_s: float = 20.0
    pad_s: float = 5.0
    split_dip_db: float = 6.0
    manual_windows: tuple = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, d: dict) -> "DetectParams":
        kw = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "band_hz" in kw:
            kw["band_hz"] = tuple(kw["band_hz"])
        if "manual_windows" in kw:
            kw["manual_windows"] = tuple(kw["manual_windows"] or ())
        return cls(**kw)


@dataclass(frozen=True)
class EventWindow:
    """A candidate flyover: recording-relative time span and the band to search."""

    start_s: float
    end_s: float
    band_hz: tuple[float, float]
    peak_s: float = float("nan")
    peak_excess_db: float = float("nan")
    manual: bool = False


def tonal_excess(spec: Spectrogram, band_hz: tuple[float, float], smooth_s: float) -> NDArray:
    """Per-frame max (over band) of dB excess above the per-bin median, median-smoothed in time."""
    m = (spec.f_hz >= band_hz[0]) & (spec.f_hz <= band_hz[1])
    Z = spec.power_db[m] - spec.noise_floor_db()[m, None]
    stat = Z.max(axis=0)
    k = max(1, int(round(smooth_s / spec.hop_s)) | 1)
    return median_filter(stat, size=k, mode="nearest")


def _runs(mask: NDArray) -> list[tuple[int, int]]:
    """Inclusive-exclusive index runs where mask is True."""
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def detect_events(spec: Spectrogram, params: DetectParams = DetectParams()) -> list[EventWindow]:
    """Automatic windows plus any manual ones from config (manual ones are kept as given)."""
    manual = [
        EventWindow(float(w["start_s"]), float(w["end_s"]),
                    tuple(w.get("band_hz", params.band_hz)), manual=True)
        for w in params.manual_windows
    ]
    stat = tonal_excess(spec, params.band_hz, params.smooth_s)
    base = np.percentile(stat, params.baseline_percentile)
    hop = spec.hop_s
    windows: list[EventWindow] = []
    for a, b in _runs(stat > base + params.extend_db):
        seg = stat[a:b]
        if seg.max() < base + params.trigger_db:
            continue
        # Split merged flyovers at deep dips between prominent peaks.
        pk, _ = find_peaks(np.r_[-np.inf, seg, -np.inf], prominence=params.split_dip_db,
                           distance=max(1, int(params.min_duration_s / hop)))
        pk = [p - 1 for p in pk if seg[p - 1] >= base + params.trigger_db]
        cuts = [a] + [a + p0 + int(np.argmin(seg[p0:p1])) for p0, p1 in zip(pk[:-1], pk[1:])] + [b]
        for i0, i1 in zip(cuts[:-1], cuts[1:]):
            if (i1 - i0) * hop < params.min_duration_s:
                continue
            ipk = i0 + int(np.argmax(stat[i0:i1]))
            windows.append(EventWindow(
                start_s=max(spec.t_s[0], spec.t_s[i0] - params.pad_s),
                end_s=min(spec.t_s[-1], spec.t_s[i1 - 1] + params.pad_s),
                band_hz=tuple(params.band_hz), peak_s=float(spec.t_s[ipk]),
                peak_excess_db=float(stat[ipk] - base),
            ))
    return sorted(manual + windows, key=lambda w: w.start_s)
