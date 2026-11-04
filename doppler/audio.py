"""Audio preparation, time priors and spectrograms (pure; file reading lives in cli.py).

All audio-derived times (spectrogram frames, ridge points, fitted t0) are
*recording-relative* seconds. A :class:`TimePrior` maps them to epoch seconds
— directly in ``known`` mode, via a searched offset otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy import signal
from scipy.ndimage import median_filter

TIME_MODES = ("known", "window", "relative")


def _to_epoch(s: str | float) -> float:
    if isinstance(s, (int, float)):
        return float(s)
    ts = pd.Timestamp(s)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.value / 1e9


@dataclass(frozen=True)
class TimePrior:
    """Prior on the absolute recording start time.

    known: ``start_s`` is the start (epoch s). window: start lies in
    ``[window_s[0], window_s[1]]``. relative: nothing absolute is known.
    """

    mode: str = "relative"
    start_s: float | None = None
    window_s: tuple[float, float] | None = None

    def __post_init__(self):
        if self.mode not in TIME_MODES:
            raise ValueError(f"time prior mode must be one of {TIME_MODES}, got {self.mode!r}")
        if self.mode == "known" and self.start_s is None:
            raise ValueError("known time prior needs start_s")
        if self.mode == "window" and self.window_s is None:
            raise ValueError("window time prior needs window_s")

    def to_absolute(self, t_rel_s: NDArray | float, offset_s: float = 0.0) -> NDArray | float:
        """Recording-relative -> epoch seconds (known mode; ``offset_s`` refines the start)."""
        if self.start_s is None:
            raise ValueError(f"no absolute start in {self.mode!r} mode; supply a searched start")
        return np.asarray(t_rel_s) + self.start_s + offset_s


def parse_time_prior(d: dict) -> TimePrior:
    """Build a TimePrior from a config/sidecar dict (ISO-8601 UTC strings or epoch floats)."""
    mode = d.get("mode", "relative")
    start = d.get("start_utc", d.get("start_s"))
    window = d.get("window_utc", d.get("window_s"))
    return TimePrior(
        mode=mode,
        start_s=_to_epoch(start) if (start is not None and mode == "known") else None,
        window_s=(_to_epoch(window[0]), _to_epoch(window[1])) if (window and mode == "window") else None,
    )


def mix_to_mono(x: NDArray) -> NDArray:
    """(N,) or (N, channels) -> (N,) float64 mean of channels."""
    x = np.asarray(x, dtype=np.float64)
    return x if x.ndim == 1 else x.mean(axis=1)


@dataclass(frozen=True)
class SpectrogramParams:
    analysis_fs_hz: float = 2000.0
    window_s: float = 1.0
    overlap: float = 0.9
    fmax_hz: float = 1000.0

    @classmethod
    def from_dict(cls, d: dict) -> "SpectrogramParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Spectrogram:
    """Power spectrogram in dB. ``power_db`` has shape (n_freq, n_time)."""

    t_s: NDArray        # frame centres, recording-relative
    f_hz: NDArray
    power_db: NDArray
    window_s: float
    hop_s: float

    @property
    def df_hz(self) -> float:
        return float(self.f_hz[1] - self.f_hz[0])

    def noise_floor_db(self) -> NDArray:
        """Per-frequency median over time (robust to tones occupying a minority of frames)."""
        return np.median(self.power_db, axis=1)


def resample(x: NDArray, fs_in: float, fs_out: float) -> NDArray:
    """Polyphase resample (anti-aliased)."""
    if fs_in == fs_out:
        return x
    fr = Fraction(fs_out / fs_in).limit_denominator(1000)
    return signal.resample_poly(x, fr.numerator, fr.denominator)


def compute_spectrogram(x: NDArray, fs_hz: float, params: SpectrogramParams = SpectrogramParams(),
                        t_range_s: tuple[float, float] | None = None) -> Spectrogram:
    """Hann-window PSD spectrogram (scipy.signal.spectrogram), cropped to fmax, in dB.

    ``t_range_s`` restricts the computation to a recording-relative span (frame
    times stay recording-relative), so long clips can be processed window by
    window with memory proportional to the span.
    """
    x = mix_to_mono(x)
    t_off = 0.0
    if t_range_s is not None:
        i0 = max(0, int(np.floor(t_range_s[0] * fs_hz)))
        i1 = min(len(x), int(np.ceil(t_range_s[1] * fs_hz)))
        x, t_off = x[i0:i1], i0 / fs_hz
    fs = float(min(params.analysis_fs_hz, fs_hz))
    x = resample(x, fs_hz, fs)
    nper = int(round(params.window_s * fs))
    nover = int(round(params.overlap * nper))
    f, t, S = signal.spectrogram(x, fs=fs, window="hann", nperseg=nper, noverlap=nover,
                                 scaling="density", mode="psd", detrend=False)
    keep = f <= params.fmax_hz
    S = S[keep].astype(np.float32)
    db = 10 * np.log10(S + np.finfo(np.float32).tiny)
    return Spectrogram(t_s=t + t_off, f_hz=f[keep], power_db=db, window_s=nper / fs, hop_s=(nper - nover) / fs)


FLOOR_MODES = ("freq", "time")


def background_db(spec: Spectrogram, mode: str = "freq", halfwidth_hz: float = 20.0) -> NDArray:
    """Background level to measure tonal excess against.

    freq: per frame, a running median across ±halfwidth_hz. Needs no quiet
          frames, so it works for any clip length, and broadband aircraft noise
          cancels (only tonality remains). Returns (F, T).
    time: per bin, the median over the clip. Needs the tone to occupy a minority
          of frames (long clips). Returns (F, 1).
    """
    if mode == "time":
        return np.median(spec.power_db, axis=1, keepdims=True)
    if mode != "freq":
        raise ValueError(f"floor mode must be one of {FLOOR_MODES}")
    k = max(3, int(round(2 * halfwidth_hz / spec.df_hz)) | 1)
    return running_median_freq(spec.power_db, k)


def running_median_freq(P: NDArray, k: int, step: int | None = None) -> NDArray:
    """Running median along axis 0 with window k, evaluated every ``step`` bins and
    linearly interpolated (≈10x faster than a full median filter, same result for
    a background that varies slowly across frequency)."""
    F = P.shape[0]
    step = step or max(1, k // 4)
    half = k // 2
    Pp = np.pad(P, ((half, half), (0, 0)), mode="edge")
    centres = np.arange(0, F, step)
    if centres[-1] != F - 1:
        centres = np.r_[centres, F - 1]
    win = np.lib.stride_tricks.sliding_window_view(Pp, k, axis=0)[centres]  # (C, T, k)
    med = np.median(win, axis=-1)
    if step == 1:
        return med
    idx = np.arange(F)
    j = np.searchsorted(centres, idx, side="right") - 1
    j = np.clip(j, 0, len(centres) - 2)
    w = ((idx - centres[j]) / (centres[j + 1] - centres[j]))[:, None]
    return ((1 - w) * med[j] + w * med[j + 1]).astype(P.dtype)
