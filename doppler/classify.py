"""Event prop/jet labelling from spectral character (rule-based, thresholds in config).

Evidence per fitted event:
  * which band its ridge came from — prop band (blade-passing / rotor tones,
    tens to hundreds of Hz) or jet band (fan tones, ~1–4 kHz);
  * harmonic count — how many of 2f, 3f, … are present along the ridge
    (propellers and rotors produce harmonic stacks);
  * broadband rise — the band-power increase around CPA relative to the clip's
    quiet level (jets are dominated by broadband noise).

Rules: a harmonic stack in the prop band → prop. A jet-band tone with a strong
broadband rise, or a strong broadband rise with no harmonic stack → jet.
Anything else → unknown, which the cascade matches against either class.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .aircraft_types import CLASS_NAMES, JET, PROP, UNKNOWN


@dataclass(frozen=True)
class ClassifyParams:
    prop_band_hz: tuple[float, float] = (40.0, 900.0)
    jet_band_hz: tuple[float, float] = (900.0, 3800.0)
    broadband_band_hz: tuple[float, float] = (150.0, 3500.0)
    broadband_percentile: float = 20.0   # clip quiet level for the broadband rise
    harmonic_weak_db: float = 8.0
    max_harmonic: int = 5
    prop_min_harmonics: int = 1          # extra harmonics (beyond the ridge) needed to call prop
    jet_min_broadband_rise_db: float = 12.0
    dedupe_dt_s: float = 3.0             # jet-band tone at the same CPA as a prop event = a prop harmonic
    dedupe_v_frac: float = 0.15

    @classmethod
    def from_dict(cls, d: dict) -> "ClassifyParams":
        kw = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k in ("prop_band_hz", "jet_band_hz", "broadband_band_hz"):
            if k in kw:
                kw[k] = tuple(kw[k])
        return cls(**kw)


@dataclass
class ClassFeatures:
    ridge_band: str            # "prop" | "jet"
    harmonics: int             # extra harmonics found along the ridge
    broadband_rise_db: float
    cls: int

    @property
    def cls_name(self) -> str:
        return CLASS_NAMES[self.cls]


def harmonic_count(full_Z: NDArray, f_axis: NDArray, t_axis: NDArray, t: NDArray, f: NDArray,
                   weak_db: float, max_k: int = 5) -> int:
    """Number of k ∈ 2..max_k whose median excess along k·f(t) is ≥ weak_db."""
    ti = np.clip(np.searchsorted(t_axis, t), 0, len(t_axis) - 1)
    n = 0
    for k in range(2, max_k + 1):
        fk = k * f
        if np.median(fk) > f_axis[-1]:
            break
        fi = np.clip(np.searchsorted(f_axis, fk), 1, len(f_axis) - 1)
        vals = np.maximum(full_Z[fi, ti], full_Z[fi - 1, ti])
        n += int(np.median(vals) >= weak_db)
    return n


def broadband_series_db(power_db: NDArray, f_axis: NDArray, band: tuple[float, float]) -> NDArray:
    """Per-frame mean power (dB) over a band."""
    m = (f_axis >= band[0]) & (f_axis <= band[1])
    return 10 * np.log10(np.mean(10 ** (power_db[m] / 10), axis=0) + 1e-30)


def broadband_rise_db(t_bb: NDArray, bb_db: NDArray, quiet_db: float, t_cpa_s: float, half_s: float) -> float:
    """Median broadband level within ±half_s of CPA, relative to the clip's quiet level."""
    m = np.abs(t_bb - t_cpa_s) <= half_s
    return float(np.median(bb_db[m]) - quiet_db) if m.any() else float("nan")


def decide(ridge_band: str, harmonics: int, bb_rise_db: float, p: ClassifyParams) -> int:
    loud = np.isfinite(bb_rise_db) and bb_rise_db >= p.jet_min_broadband_rise_db
    if ridge_band == "prop" and harmonics >= p.prop_min_harmonics:
        return PROP
    if loud and (ridge_band == "jet" or harmonics == 0):
        return JET
    return UNKNOWN
