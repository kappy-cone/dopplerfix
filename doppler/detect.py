"""Flyover event detection, for clips from under a minute to hours long.

Statistic: per frame and per configured band, the maximum over the band of the
tonal excess (spectrum minus a background level, see audio.background_db),
running-median smoothed. Each band's statistic is expressed relative to its
baseline, and the frame statistic is the max over bands (low band for prop
harmonics, high band for jet fan tones).

Baseline: in pure noise the statistic sits near a known level,
10·log10((ln N + γ)/ln 2) dB for N bins against a median background. The
baseline is the lower of that and a low percentile of the clip's statistic,
so it self-calibrates on long clips without needing quiet frames in short ones.

Windows trigger at baseline + trigger_db, extend while above baseline +
extend_db, and merged flyovers are split at deep dips. ``detect_events_audio``
does this in chunks so memory does not grow with clip length.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import median_filter
from scipy.signal import find_peaks

from .audio import Spectrogram, SpectrogramParams, background_db, compute_spectrogram

EULER_GAMMA = 0.5772156649


@dataclass(frozen=True)
class DetectParams:
    band_hz: tuple[float, float] = (40.0, 900.0)  # band reported on windows (ridge search default)
    bands_hz: tuple = ((40.0, 900.0),)            # bands searched for tonal excess (main spectrogram)
    jet_bands_hz: tuple = ()                      # bands searched on the short-window jet spectrogram
    floor_mode: str = "freq"
    floor_halfwidth_hz: float = 20.0
    smooth_s: float = 3.0
    trigger_db: float = 10.0       # above baseline
    extend_db: float = 4.0         # above baseline
    baseline_percentile: float = 20.0
    baseline_theory_slack_db: float = 1.0
    min_duration_s: float = 20.0
    pad_s: float = 5.0
    split_dip_db: float = 6.0
    chunk_s: float = 120.0         # audio processed in chunks of this length (with overlap)
    broadband_trigger: bool = True # also detect broadband rises (jets) vs the clip's quiet level
    manual_windows: tuple = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, d: dict) -> "DetectParams":
        kw = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "band_hz" in kw:
            kw["band_hz"] = tuple(kw["band_hz"])
        if "bands_hz" in kw:
            kw["bands_hz"] = tuple(tuple(b) for b in kw["bands_hz"])
        if "jet_bands_hz" in kw:
            kw["jet_bands_hz"] = tuple(tuple(b) for b in (kw["jet_bands_hz"] or ()))
        elif "band_hz" in kw:
            kw["bands_hz"] = (kw["band_hz"],)
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


def theoretical_baseline_db(n_bins: int) -> float:
    """Typical max over n exponential bins relative to their median, in dB."""
    return float(10 * np.log10((np.log(max(n_bins, 2)) + EULER_GAMMA) / np.log(2)))


def tonal_statistic(spec: Spectrogram, params: DetectParams) -> NDArray:
    """(n_bands, T) per-band max tonal excess (dB), unsmoothed, with no baseline removed.

    The background is computed only on each band's rows (plus the median half-width),
    so a wide analysis bandwidth doesn't cost memory outside the bands.
    """
    out = []
    pad = params.floor_halfwidth_hz
    for lo, hi in params.bands_hz:
        rows = (spec.f_hz >= lo - pad) & (spec.f_hz <= hi + pad)
        if not ((spec.f_hz >= lo) & (spec.f_hz <= hi)).any():
            out.append(np.full(spec.t_s.size, -np.inf))
            continue
        sub = Spectrogram(spec.t_s, spec.f_hz[rows], spec.power_db[rows], spec.window_s, spec.hop_s)
        Z = sub.power_db - background_db(sub, params.floor_mode, params.floor_halfwidth_hz)
        m = (sub.f_hz >= lo) & (sub.f_hz <= hi)
        out.append(Z[m].max(axis=0))
    return np.array(out)


def _bins_per_band(f_hz: NDArray, bands) -> list[int]:
    return [int(((f_hz >= lo) & (f_hz <= hi)).sum()) for lo, hi in bands]


def _runs(mask: NDArray) -> list[tuple[int, int]]:
    d = np.diff(np.r_[0, mask.astype(int), 0])
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def windows_from_statistic(t_s: NDArray, raw: NDArray, n_bins: list[int], hop_s: float,
                           params: DetectParams) -> tuple[list[EventWindow], NDArray]:
    """Smooth, baseline-normalise per band, take the max over bands and cut windows.

    Returns (windows, normalised statistic) — the statistic is useful for plots.
    """
    k = max(1, int(round(params.smooth_s / hop_s)) | 1)
    norm = []
    for row, n in zip(raw, n_bins):
        sm = median_filter(row, size=k, mode="nearest")
        base = min(np.percentile(sm, params.baseline_percentile),
                   theoretical_baseline_db(n) + params.baseline_theory_slack_db)
        norm.append(sm - base)
    stat = np.max(norm, axis=0)
    windows: list[EventWindow] = []
    for a, b in _runs(stat > params.extend_db):
        seg = stat[a:b]
        if seg.max() < params.trigger_db:
            continue
        pk, _ = find_peaks(np.r_[-np.inf, seg, -np.inf], prominence=params.split_dip_db,
                           distance=max(1, int(params.min_duration_s / hop_s)))
        pk = [p - 1 for p in pk if seg[p - 1] >= params.trigger_db]
        cuts = [a] + [a + p0 + int(np.argmin(seg[p0:p1])) for p0, p1 in zip(pk[:-1], pk[1:])] + [b]
        for i0, i1 in zip(cuts[:-1], cuts[1:]):
            if (i1 - i0) * hop_s < params.min_duration_s:
                continue
            ipk = i0 + int(np.argmax(stat[i0:i1]))
            windows.append(EventWindow(
                start_s=max(t_s[0] - hop_s / 2, t_s[i0] - params.pad_s),
                end_s=min(t_s[-1] + hop_s / 2, t_s[i1 - 1] + params.pad_s),
                band_hz=tuple(params.band_hz), peak_s=float(t_s[ipk]), peak_excess_db=float(stat[ipk]),
            ))
    return windows, stat


def _manual(params: DetectParams) -> list[EventWindow]:
    return [EventWindow(float(w["start_s"]), float(w["end_s"]), tuple(w.get("band_hz", params.band_hz)), manual=True)
            for w in params.manual_windows]


def detect_events(spec: Spectrogram, params: DetectParams = DetectParams()) -> list[EventWindow]:
    """Detect on an in-memory spectrogram (automatic windows + manual ones from config)."""
    raw = tonal_statistic(spec, params)
    wins, _ = windows_from_statistic(spec.t_s, raw, _bins_per_band(spec.f_hz, params.bands_hz), spec.hop_s, params)
    return sorted(_manual(params) + wins, key=lambda w: w.start_s)


@dataclass
class Detection:
    """Result of chunked detection over a whole clip."""

    windows: list[EventWindow]
    t_s: NDArray            # frame times
    stat: NDArray           # normalised tonal statistic (dB above baseline)
    broadband_db: NDArray   # per-frame mean power over the broadband band (for classification)
    broadband_band_hz: tuple[float, float]


def detect_events_audio(x: NDArray, fs_hz: float, spec_params: SpectrogramParams = SpectrogramParams(),
                        params: DetectParams = DetectParams(),
                        broadband_band_hz: tuple[float, float] = (150.0, 3500.0),
                        jet_spec_params: SpectrogramParams | None = None) -> Detection:
    """Chunked detection straight from audio: memory ∝ chunk_s, not clip length.

    Prop bands are scored on the main spectrogram; jet bands (fast-sweeping fan
    tones) on a short-window spectrogram, max-pooled onto the main frame times.
    """
    from dataclasses import replace

    from .classify import broadband_series_db

    dur = len(x) / fs_hz
    ov = spec_params.window_s + params.smooth_s
    jet_on = bool(params.jet_bands_hz) and jet_spec_params is not None
    pj = replace(params, bands_hz=params.jet_bands_hz)
    ts, raws, bbs, n_bins, hop = [], [], [], None, None
    for a in np.arange(0.0, dur, params.chunk_s):
        b = min(a + params.chunk_s, dur)
        rng_s = (max(0.0, a - ov), min(dur, b + ov))
        sp = compute_spectrogram(x, fs_hz, spec_params, rng_s)
        keep = (sp.t_s >= a) & (sp.t_s < b) if b < dur else (sp.t_s >= a)
        raw = tonal_statistic(sp, params)[:, keep]
        nb = _bins_per_band(sp.f_hz, params.bands_hz)
        if jet_on:
            sj = compute_spectrogram(x, fs_hz, jet_spec_params, rng_s)
            rj = tonal_statistic(sj, pj)
            # max-pool the jet statistic over each main frame's span
            idx = np.clip(np.searchsorted(sp.t_s[keep], sj.t_s), 0, keep.sum() - 1)
            pooled = np.full((rj.shape[0], keep.sum()), -np.inf)
            for r in range(rj.shape[0]):
                np.maximum.at(pooled[r], idx, rj[r])
            raw = np.vstack([raw, pooled])
            nb = nb + _bins_per_band(sj.f_hz, params.jet_bands_hz)
        raws.append(raw)
        bbs.append(broadband_series_db(sp.power_db[:, keep], sp.f_hz, broadband_band_hz))
        ts.append(sp.t_s[keep])
        n_bins, hop = nb, sp.hop_s
    t = np.concatenate(ts)
    raw = np.concatenate(raws, axis=1)
    bb = np.concatenate(bbs)
    if params.broadband_trigger:
        # broadband row: baseline from the percentile only (no theory), so it never fires on a
        # clip that is loud throughout — tonal rows still cover that case
        raw = np.vstack([raw, bb[None, :]])
        n_bins = list(n_bins) + [10**9]
    wins, stat = windows_from_statistic(t, raw, n_bins, hop, params)
    return Detection(sorted(_manual(params) + wins, key=lambda w: w.start_s), t, stat, bb, tuple(broadband_band_hz))
