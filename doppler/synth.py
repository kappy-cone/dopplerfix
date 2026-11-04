"""Synthetic scenarios: tracks + hidden mic + audio with exact propagation delay.

Audio is synthesised on the *received* time axis by evaluating the source phase
at the emission time t_e(t_r), i.e. the Doppler shift and propagation delay are
modelled exactly rather than by integrating an approximate frequency curve.
Everything here is pure (no file I/O); see export.write_scenario.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.interpolate import CubicSpline
from scipy.ndimage import gaussian_filter1d

from .geo import LocalFrame
from .physics import DEFAULT_C
from .tracks import Track


@dataclass
class TrackSpec:
    """Kinematic description of one synthetic flyover.

    The aircraft passes the point ``mic + offset_m · n̂`` at ``t_cpa_rel_s``
    (relative to recording start), where n̂ points to the right of the track.
    ``offset_m`` is signed: positive puts the mic to the left of the track.
    With a turn or climb the true CPA drifts slightly; truth is always
    recomputed from geometry.
    """

    icao24: str
    callsign: str
    t_cpa_rel_s: float
    heading_deg: float
    speed_mps: float
    alt_agl_m: float
    offset_m: float
    f0_hz: float
    turn_rate_deg_s: float = 0.0
    climb_mps: float = 0.0
    turn_half_s: float = 60.0    # the turn lasts from t_cpa − turn_half_s to t_cpa + turn_half_s
    snr_db: float | None = None  # override SynthParams.snr_db


@dataclass
class SynthParams:
    seed: int = 0
    duration_s: float = 600.0
    fs_hz: int = 8000
    n_tracks: int = 3
    snr_db: float = 10.0
    noise_band_hz: tuple[float, float] = (40.0, 1000.0)
    harmonic_amps: tuple[float, ...] = (1.0, 0.5, 0.3)
    f0_range_hz: tuple[float, float] = (80.0, 180.0)
    speed_range_mps: tuple[float, float] = (55.0, 90.0)
    alt_agl_range_m: tuple[float, float] = (300.0, 900.0)
    offset_range_m: tuple[float, float] = (150.0, 1200.0)
    excess_atten_db_per_km: float = 3.0
    f0_jitter_frac: float = 0.002
    broadband_to_tone_db: float = 0.0
    curved_fraction: float = 0.34
    track_dt_s: float = 0.25
    track_pad_before_s: float = 180.0
    track_pad_after_s: float = 60.0
    mic_height_m: float = 1.5
    mic_max_offset_m: float = 3000.0  # hidden mic is placed within this of the region centre

    @classmethod
    def from_dict(cls, d: dict) -> "SynthParams":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k, v in known.items():
            if isinstance(v, list):
                known[k] = tuple(v)
        return cls(**known)


@dataclass
class EventTruth:
    """Ground truth for one flyover, in recording-relative time."""

    icao24: str
    t0_rel_s: float               # emission time of closest approach
    t_cpa_received_rel_s: float   # when the CPA sound arrives at the mic
    d_m: float                    # 3-D slant range at CPA
    v_mps: float                  # 3-D airspeed at CPA (no wind in synth)
    f0_hz: float
    h_offset_m: float             # horizontal distance at CPA
    height_above_mic_m: float


@dataclass
class Scenario:
    frame: LocalFrame
    start_time_s: float           # recording start, epoch seconds
    fs_hz: int
    audio: NDArray
    tracks: list[Track]
    track_enu: list[NDArray]
    mic_enu: NDArray
    mic_lat: float
    mic_lon: float
    ground_elev_m: float
    specs: list[TrackSpec]
    truth: list[EventTruth]
    c: float
    params: SynthParams = field(repr=False)


# ---------------------------------------------------------------------------
# Tracks
# ---------------------------------------------------------------------------
def random_track_specs(rng: np.random.Generator, p: SynthParams) -> list[TrackSpec]:
    """Spread CPAs over the recording and headings around the compass."""
    n = p.n_tracks
    slot = p.duration_s / n
    base_heading = rng.uniform(0, 360)
    n_curved = int(round(p.curved_fraction * n))
    curved = set(rng.choice(n, size=n_curved, replace=False).tolist()) if n_curved else set()
    specs = []
    for i in range(n):
        heading = (base_heading + i * 180.0 / n + rng.uniform(-15, 15)) % 360
        heading = (heading + 180.0 * rng.integers(0, 2)) % 360  # either direction
        specs.append(TrackSpec(
            icao24=f"{rng.integers(0, 2**24):06x}",
            callsign=f"SYN{i + 1:03d}",
            t_cpa_rel_s=(i + 0.5) * slot + rng.uniform(-0.15, 0.15) * slot,
            heading_deg=float(heading),
            speed_mps=float(rng.uniform(*p.speed_range_mps)),
            alt_agl_m=float(rng.uniform(*p.alt_agl_range_m)),
            offset_m=float(rng.uniform(*p.offset_range_m) * rng.choice([-1, 1])),
            f0_hz=float(rng.uniform(*p.f0_range_hz)),
            turn_rate_deg_s=float(rng.uniform(0.3, 0.8) * rng.choice([-1, 1])) if i in curved else 0.0,
        ))
    return specs


def build_track_enu(spec: TrackSpec, mic_enu: NDArray, ground_elev_m: float, t_rel: NDArray) -> NDArray:
    """Aircraft ENU positions (N, 3).

    Straight and level, except that the heading changes at ``turn_rate_deg_s``
    for ``|t − t_cpa| < turn_half_s`` (a turn through the pass, then roll-out),
    and altitude changes at ``climb_mps``. The pass point is pinned at t_cpa.
    """
    psi0 = np.deg2rad(spec.heading_deg)
    v = spec.speed_mps
    n_hat = np.array([np.cos(psi0), -np.sin(psi0)])  # right of track
    p0 = mic_enu[:2] + spec.offset_m * n_hat
    dt = t_rel - spec.t_cpa_rel_s
    w = np.deg2rad(spec.turn_rate_deg_s)
    psi = psi0 + w * np.clip(dt, -spec.turn_half_s, spec.turn_half_s)
    vx, vy = v * np.sin(psi), v * np.cos(psi)
    # Integrate velocity (trapezoid) and pin the position at t_cpa.
    x = np.concatenate([[0.0], np.cumsum(0.5 * (vx[1:] + vx[:-1]) * np.diff(t_rel))])
    y = np.concatenate([[0.0], np.cumsum(0.5 * (vy[1:] + vy[:-1]) * np.diff(t_rel))])
    x += p0[0] - np.interp(spec.t_cpa_rel_s, t_rel, x)
    y += p0[1] - np.interp(spec.t_cpa_rel_s, t_rel, y)
    z = ground_elev_m + spec.alt_agl_m + spec.climb_mps * dt
    return np.c_[x, y, z]


def event_truth(icao24: str, f0_hz: float, t_rel: NDArray, enu: NDArray, mic_enu: NDArray,
                c: float, t_near_s: float | None = None, search_s: float = 90.0) -> EventTruth:
    """CPA truth from geometry, refined by dense resampling around the sampled minimum.

    With ``t_near_s`` the minimum is searched within ±search_s of it (a turning
    track can come close again elsewhere).
    """
    r = np.linalg.norm(enu - mic_enu, axis=1)
    if t_near_s is not None:
        r = np.where(np.abs(t_rel - t_near_s) <= search_s, r, np.inf)
    i = int(np.argmin(r))
    lo, hi = max(i - 4, 0), min(i + 5, len(t_rel))
    tf = np.linspace(t_rel[lo], t_rel[hi - 1], 4001)
    spl = CubicSpline(t_rel, enu, axis=0)
    pf = spl(tf)
    rf = np.linalg.norm(pf - mic_enu, axis=1)
    # (spline over the full track; only the local window is evaluated)
    j = int(np.argmin(rf))
    t0, p = tf[j], pf[j]
    vel = spl(t0, 1)
    d = float(rf[j])
    return EventTruth(
        icao24=icao24, t0_rel_s=float(t0), t_cpa_received_rel_s=float(t0 + d / c), d_m=d,
        v_mps=float(np.linalg.norm(vel)), f0_hz=f0_hz,
        h_offset_m=float(np.hypot(*(p[:2] - mic_enu[:2]))),
        height_above_mic_m=float(p[2] - mic_enu[2]),
    )


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
def band_power(x: NDArray, fs: float, band: tuple[float, float]) -> float:
    """Mean power of x within a frequency band (Parseval on the rFFT)."""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / fs)
    m = (f >= band[0]) & (f <= band[1])
    return float(2 * np.sum(np.abs(X[m]) ** 2) / len(x) ** 2)


def colored_noise(rng: np.random.Generator, n: int, fs: float, pink_to_white_db: float = 10.0) -> NDArray:
    """Pink (1/f power) plus white background noise, unit-ish scale."""
    white = rng.standard_normal(n)
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / fs)
    X *= 1.0 / np.sqrt(np.maximum(f, 20.0))
    pink = np.fft.irfft(X, n)
    pink *= 10 ** (pink_to_white_db / 20) * np.std(white) / np.std(pink)
    return pink + white


def bandlimited_noise(rng: np.random.Generator, n: int, fs: float, band: tuple[float, float]) -> NDArray:
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / fs)
    X[(f < band[0]) | (f > band[1])] = 0
    x = np.fft.irfft(X, n)
    return x / np.std(x)


def flyover_audio(
    rng: np.random.Generator, spec: TrackSpec, truth: EventTruth, t_track_rel: NDArray, enu: NDArray,
    mic_enu: NDArray, t_audio_rel: NDArray, fs: float, noise_band_power: float, p: SynthParams, c: float,
) -> NDArray:
    """One aircraft's contribution at the mic: delayed, Doppler-shifted harmonics + broadband."""
    r = np.linalg.norm(enu - mic_enu, axis=1)
    t_r = t_track_rel + r / c
    # Slowly wandering engine tone.
    jitter = gaussian_filter1d(rng.standard_normal(t_track_rel.size), sigma=20.0 / p.track_dt_s)
    jitter *= p.f0_jitter_frac / max(np.std(jitter), 1e-12)
    phase_e = 2 * np.pi * spec.f0_hz * np.concatenate(
        [[0.0], np.cumsum(0.5 * ((1 + jitter[1:]) + (1 + jitter[:-1])) * np.diff(t_track_rel))]
    )
    valid = (t_audio_rel >= t_r[0]) & (t_audio_rel <= t_r[-1])
    tq = t_audio_rel[valid]
    phase = CubicSpline(t_r, phase_e)(tq)
    rq = np.interp(tq, t_r, r)

    snr_db = p.snr_db if spec.snr_db is None else spec.snr_db
    amps = np.asarray(p.harmonic_amps, float)
    tone_power_cpa = noise_band_power * 10 ** (snr_db / 10)
    a_cpa = np.sqrt(2 * tone_power_cpa / np.sum(amps**2))
    d = truth.d_m
    env = a_cpa * (d / rq) * 10 ** (-p.excess_atten_db_per_km * (rq - d) / 1000 / 20)

    sig = np.zeros_like(tq)
    for k, a in enumerate(amps, start=1):
        sig += a * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
    out = np.zeros_like(t_audio_rel)
    out[valid] = env * sig
    if p.broadband_to_tone_db > -np.inf:
        bb = bandlimited_noise(rng, t_audio_rel.size, fs, p.noise_band_hz)
        bb_scale = np.sqrt(tone_power_cpa * 10 ** (p.broadband_to_tone_db / 10))
        out[valid] += (env / a_cpa) * bb_scale * bb[valid]
    # 0.5 s fades where the track (and hence the sound) starts/ends.
    fade = np.clip(np.minimum(tq - t_r[0], t_r[-1] - tq) / 0.5, 0, 1)
    out[valid] *= fade
    return out


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------
def make_scenario(
    params: SynthParams,
    frame: LocalFrame,
    start_time_s: float,
    ground_elev_m: float,
    c: float = DEFAULT_C,
    mic_enu: NDArray | None = None,
    specs: list[TrackSpec] | None = None,
) -> Scenario:
    """Build tracks, a hidden mic and the recording. Deterministic given ``params.seed``."""
    rng = np.random.default_rng(params.seed)
    if mic_enu is None:
        rad = params.mic_max_offset_m * np.sqrt(rng.uniform())
        ang = rng.uniform(0, 2 * np.pi)
        mic_enu = np.array([rad * np.cos(ang), rad * np.sin(ang), 0.0])
    mic_enu = np.array([mic_enu[0], mic_enu[1], ground_elev_m + params.mic_height_m - frame.h0], float)
    if specs is None:
        specs = random_track_specs(rng, params)

    fs = params.fs_hz
    n = int(round(params.duration_s * fs))
    t_audio = np.arange(n) / fs
    t_track = np.arange(-params.track_pad_before_s, params.duration_s + params.track_pad_after_s + 1e-9,
                        params.track_dt_s)
    ground_rel = ground_elev_m - frame.h0

    noise = colored_noise(rng, n, fs)
    p_noise = band_power(noise, fs, params.noise_band_hz)
    audio = noise.copy()
    tracks, enus, truths = [], [], []
    for spec in specs:
        enu = build_track_enu(spec, mic_enu, ground_rel, t_track)
        tru = event_truth(spec.icao24, spec.f0_hz, t_track, enu, mic_enu, c, t_near_s=spec.t_cpa_rel_s)
        audio += flyover_audio(rng, spec, tru, t_track, enu, mic_enu, t_audio, fs, p_noise, params, c)
        tracks.append(Track.from_enu(spec.icao24, spec.callsign, start_time_s + t_track, enu, frame))
        enus.append(enu)
        truths.append(tru)
    audio = (0.9 * audio / np.max(np.abs(audio))).astype(np.float32)
    lat, lon, _ = frame.to_geodetic(mic_enu)
    return Scenario(
        frame=frame, start_time_s=start_time_s, fs_hz=fs, audio=audio, tracks=tracks, track_enu=enus,
        mic_enu=mic_enu, mic_lat=float(lat), mic_lon=float(lon), ground_elev_m=ground_elev_m,
        specs=specs, truth=truths, c=c, params=params,
    )
