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


def compute_spectrogram(x: NDArray, fs_hz: float, params: SpectrogramParams = SpectrogramParams()) -> Spectrogram:
    """Hann-window PSD spectrogram (scipy.signal.spectrogram), cropped to fmax, in dB."""
    x = mix_to_mono(x)
    fs = float(min(params.analysis_fs_hz, fs_hz))
    x = resample(x, fs_hz, fs)
    nper = int(round(params.window_s * fs))
    nover = int(round(params.overlap * nper))
    f, t, S = signal.spectrogram(x, fs=fs, window="hann", nperseg=nper, noverlap=nover,
                                 scaling="density", mode="psd", detrend=False)
    keep = f <= params.fmax_hz
    S = S[keep].astype(np.float32)
    db = 10 * np.log10(S + np.finfo(np.float32).tiny)
    return Spectrogram(t_s=t, f_hz=f[keep], power_db=db, window_s=nper / fs, hop_s=(nper - nover) / fs)
