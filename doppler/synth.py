"""Synthetic scenarios: tracks + hidden mic + audio with exact propagation delay.

Audio is synthesised on the *received* time axis by evaluating each source at
its emission time t_e(t_r), so Doppler shift and propagation delay are exact
(for tones via phase, for jet broadband noise via time-warping).

Track roles
-----------
* ``event``      — audible at the mic; its flyover is in the audio.
* ``decoy``      — passes audibly over a confusable cell (e.g. the mirror image of
                   the mic across an event track) during the clip while staying
                   beyond the audible threshold of the true mic: the silence filter
                   must remove that cell.
* ``twin``       — same class and similar speed as an event, elsewhere and
                   inaudible: survives the CPA prefilter somewhere, must die later.
* ``background`` — other inaudible traffic.

Loudness is physical: each source has a level at a reference distance and
falls off as 1/r plus excess attenuation, so far aircraft are quiet. Event
sources can instead be given an SNR at their own CPA (the knob the fit tests use).
Everything here is pure (no file I/O); see export.write_scenario.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray
from scipy.interpolate import CubicSpline
from scipy.ndimage import gaussian_filter1d

from .geo import LocalFrame, enu_basis, enu_vector_to_ecef
from .physics import DEFAULT_C
from .tracks import Track

# Representative ICAO type designators with their Doc 8643 descriptions.
PROP_TYPES = {"C172": "L1P", "PA28": "L1P", "SR22": "L1P", "DH8D": "L2T", "B350": "L2T", "R44": "H1P"}
JET_TYPES = {"B738": "L2J", "A320": "L2J", "CRJ9": "L2J", "E75L": "L2J", "C56X": "L2J"}
ROLES = ("event", "decoy", "twin", "background")


@dataclass
class TrackSpec:
    """Kinematic and acoustic description of one synthetic aircraft.

    The aircraft passes ``anchor + offset_m · n̂`` at ``t_cpa_rel_s`` (emission
    time, recording-relative), n̂ pointing right of track; ``anchor`` is the mic
    unless ``anchor_enu`` is set (decoys). Instead of ``t_cpa_rel_s`` you may give
    ``t_rx_cpa_rel_s``: the time the CPA sound should *arrive* at the anchor; the
    emission time is then solved from the geometry.
    """

    icao24: str
    callsign: str
    t_cpa_rel_s: float | None
    heading_deg: float
    speed_mps: float
    alt_agl_m: float
    offset_m: float
    f0_hz: float
    turn_rate_deg_s: float = 0.0
    climb_mps: float = 0.0
    turn_half_s: float = 60.0
    snr_db: float | None = None          # SNR at this aircraft's CPA to the mic (events)
    cls: str = "prop"                    # prop | jet
    type_code: str = ""
    role: str = "event"
    t_rx_cpa_rel_s: float | None = None
    anchor_enu: tuple[float, float] | None = None


@dataclass
class SynthParams:
    seed: int = 0
    duration_s: float = 600.0
    fs_hz: int = 8000
    n_tracks: int = 3                    # legacy layout: events spread evenly (when event_rx_times_s is None)
    event_rx_times_s: tuple[float, ...] | None = None  # received CPA times of the events, recording-relative
    event_classes: tuple[str, ...] | None = None       # per event; default all prop
    snr_db: float = 10.0                 # event SNR at its own CPA vs noise power in noise_band_hz
    noise_band_hz: tuple[float, float] = (40.0, 1000.0)
    harmonic_amps: tuple[float, ...] = (1.0, 0.5, 0.3)
    f0_range_hz: tuple[float, float] = (80.0, 180.0)
    speed_range_mps: tuple[float, float] = (55.0, 90.0)
    alt_agl_range_m: tuple[float, float] = (300.0, 900.0)
    offset_range_m: tuple[float, float] = (150.0, 1200.0)
    jet_f0_range_hz: tuple[float, float] = (900.0, 2200.0)   # fan tone
    jet_harmonic_amps: tuple[float, ...] = (1.0, 0.3)
    jet_speed_range_mps: tuple[float, float] = (75.0, 130.0)
    jet_alt_agl_range_m: tuple[float, float] = (400.0, 1500.0)
    jet_broadband_band_hz: tuple[float, float] = (150.0, 3500.0)
    jet_broadband_to_tone_db: float = 5.0   # total broadband power vs fan-tone power
    excess_atten_db_per_km: float = 3.0
    f0_jitter_frac: float = 0.002
    broadband_to_tone_db: float = 0.0    # prop broadband component
    curved_fraction: float = 0.34
    ref_distance_m: float = 1000.0
    prop_snr_at_ref_db: float = -5.0     # non-event source levels: tones at 1.2x threshold stay below ridge weak_db
    jet_snr_at_ref_db: float = 12.0
    audible_slant_m: dict = field(default_factory=lambda: {"prop": 3000.0, "jet": 6000.0, "unknown": 6000.0})
    event_max_slant_frac: float = 0.6    # events pass within this fraction of their audible threshold
    n_decoys_per_event: int = 0
    n_twins_per_event: int = 0
    n_background: int = 0
    decoy_target_slant_frac: float = 0.8 # decoy must pass this close (× threshold) to its target cell
    inaudible_margin: float = 1.2        # non-events stay ≥ margin × threshold from the true mic
    region_radius_m: float = 6000.0
    skip_audio_beyond_m: float = 8000.0  # below -25 dB re detection: not worth synthesising
    unknown_type_frac: float = 0.0       # share of aircraft left out of the synthetic registry
    track_dt_s: float = 0.25
    track_pad_before_s: float = 180.0
    track_pad_after_s: float = 60.0
    mic_height_m: float = 1.5
    mic_max_offset_m: float = 3000.0
    n_cascade_decoys: int = 0            # decoys over cascade (CPA+offset) survivors; used by cli synth

    @classmethod
    def from_dict(cls, d: dict) -> "SynthParams":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        for k, v in known.items():
            if isinstance(v, list):
                known[k] = tuple(v)
        return cls(**known)

    def threshold(self, cls_name: str) -> float:
        return float(self.audible_slant_m.get(cls_name, self.audible_slant_m.get("unknown")))


@dataclass
class EventTruth:
    """Ground truth for one audible flyover, in recording-relative time."""

    icao24: str
    t0_rel_s: float               # emission time of closest approach
    t_cpa_received_rel_s: float   # when the CPA sound arrives at the mic
    d_m: float                    # 3-D slant range at CPA
    v_mps: float                  # 3-D airspeed at CPA (no wind in synth)
    f0_hz: float
    h_offset_m: float             # horizontal distance at CPA
    height_above_mic_m: float
    cls: str = "prop"


@dataclass
class DecoyTruth:
    """What a non-event track is for and how close it gets."""

    icao24: str
    role: str
    cls: str
    event_index: int | None               # the event it was built against
    target_enu: tuple[float, float] | None  # decoys: the cell they must eliminate
    min_slant_to_mic_m: float             # over the clip's received-time window
    min_slant_to_target_m: float | None


@dataclass
class Scenario:
    frame: LocalFrame
    start_time_s: float           # recording start, epoch seconds
    fs_hz: int
    audio: NDArray
    tracks: list[Track]           # all roles; Track.cls left UNKNOWN (pipeline uses the registry)
    track_enu: list[NDArray]
    mic_enu: NDArray
    mic_lat: float
    mic_lon: float
    ground_elev_m: float
    specs: list[TrackSpec]
    truth: list[EventTruth]       # events only, in spec order
    c: float
    params: SynthParams = field(repr=False)
    decoys: list[DecoyTruth] = field(default_factory=list)
    registry: pd.DataFrame | None = None

    @property
    def event_specs(self) -> list[TrackSpec]:
        return [s for s in self.specs if s.role == "event"]

    @property
    def mic_ecef(self) -> NDArray:
        return self.frame.enu_to_ecef(self.mic_enu)

    def track_time_rel(self) -> NDArray:
        return self.tracks[0].t_s - self.start_time_s


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def build_track_enu(spec: TrackSpec, mic_enu: NDArray, ground_elev_m: float, t_rel: NDArray) -> NDArray:
    """Aircraft ENU positions (N, 3).

    Straight and level, except that the heading changes at ``turn_rate_deg_s``
    for ``|t − t_cpa| < turn_half_s`` (a turn through the pass, then roll-out),
    and altitude changes at ``climb_mps``. The pass point is pinned at t_cpa,
    relative to ``spec.anchor_enu`` if set, else the mic.
    """
    anchor = np.asarray(spec.anchor_enu if spec.anchor_enu is not None else mic_enu[:2], float)
    psi0 = np.deg2rad(spec.heading_deg)
    v = spec.speed_mps
    n_hat = np.array([np.cos(psi0), -np.sin(psi0)])  # right of track
    p0 = anchor + spec.offset_m * n_hat
    dt = t_rel - spec.t_cpa_rel_s
    w = np.deg2rad(spec.turn_rate_deg_s)
    psi = psi0 + w * np.clip(dt, -spec.turn_half_s, spec.turn_half_s)
    vx, vy = v * np.sin(psi), v * np.cos(psi)
    x = np.concatenate([[0.0], np.cumsum(0.5 * (vx[1:] + vx[:-1]) * np.diff(t_rel))])
    y = np.concatenate([[0.0], np.cumsum(0.5 * (vy[1:] + vy[:-1]) * np.diff(t_rel))])
    x += p0[0] - np.interp(spec.t_cpa_rel_s, t_rel, x)
    y += p0[1] - np.interp(spec.t_cpa_rel_s, t_rel, y)
    z = ground_elev_m + spec.alt_agl_m + spec.climb_mps * dt
    return np.c_[x, y, z]


def event_truth(icao24: str, f0_hz: float, t_rel: NDArray, enu: NDArray, mic_enu: NDArray,
                c: float, t_near_s: float | None = None, search_s: float = 90.0, cls: str = "prop") -> EventTruth:
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
    j = int(np.argmin(rf))
    t0, p = tf[j], pf[j]
    vel = spl(t0, 1)
    d = float(rf[j])
    return EventTruth(
        icao24=icao24, t0_rel_s=float(t0), t_cpa_received_rel_s=float(t0 + d / c), d_m=d,
        v_mps=float(np.linalg.norm(vel)), f0_hz=f0_hz,
        h_offset_m=float(np.hypot(*(p[:2] - mic_enu[:2]))),
        height_above_mic_m=float(p[2] - mic_enu[2]), cls=cls,
    )


def min_slant_in_clip(enu: NDArray, t_rel: NDArray, point_enu: NDArray, c: float, duration_s: float) -> float:
    """Closest slant range from a track to a point among sounds received during [0, duration]."""
    r = np.linalg.norm(enu - point_enu, axis=1)
    t_r = t_rel + r / c
    m = (t_r >= 0) & (t_r <= duration_s)
    return float(r[m].min()) if m.any() else float("inf")


def mirror_point(enu: NDArray, t_rel: NDArray, t0_rel_s: float, mic_enu: NDArray) -> NDArray:
    """Reflection of the mic across the (horizontal) track line at CPA: the left/right twin cell."""
    A = np.array([np.interp(t0_rel_s, t_rel, enu[:, k]) for k in range(2)])
    vel = np.gradient(enu[:, :2], t_rel, axis=0)
    u = np.array([np.interp(t0_rel_s, t_rel, vel[:, k]) for k in range(2)])
    u /= np.linalg.norm(u)
    m = mic_enu[:2] - A
    perp = m - (m @ u) * u
    return mic_enu[:2] - 2 * perp


def resolve_rx_time(spec: TrackSpec, mic_enu: NDArray, ground_rel: float, t_rel: NDArray, c: float,
                    n_iter: int = 4) -> TrackSpec:
    """(n_iter=0: one closed-form guess, adequate for non-event tracks.)"""
    """Set ``t_cpa_rel_s`` so the CPA sound arrives at the anchor at ``t_rx_cpa_rel_s``."""
    if spec.t_rx_cpa_rel_s is None:
        return spec
    target = mic_enu.copy()
    if spec.anchor_enu is not None:
        target[:2] = spec.anchor_enu
    d_guess = np.hypot(spec.offset_m, ground_rel + spec.alt_agl_m - target[2])
    s = replace(spec, t_cpa_rel_s=spec.t_rx_cpa_rel_s - d_guess / c)
    for _ in range(n_iter):
        tru = event_truth(s.icao24, s.f0_hz, t_rel, build_track_enu(s, mic_enu, ground_rel, t_rel), target, c,
                          t_near_s=s.t_cpa_rel_s)
        err = tru.t_cpa_received_rel_s - spec.t_rx_cpa_rel_s
        if abs(err) < 1e-3:
            break
        s = replace(s, t_cpa_rel_s=s.t_cpa_rel_s - err)
    return s


# ---------------------------------------------------------------------------
# Random spec generation
# ---------------------------------------------------------------------------
def _class_ranges(p: SynthParams, cls: str):
    if cls == "jet":
        return p.jet_speed_range_mps, p.jet_alt_agl_range_m, p.jet_f0_range_hz
    return p.speed_range_mps, p.alt_agl_range_m, p.f0_range_hz


def random_track_specs(rng: np.random.Generator, p: SynthParams) -> list[TrackSpec]:
    """Legacy layout: ``n_tracks`` prop events, emission CPAs spread over the recording."""
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


def random_event_specs(rng: np.random.Generator, p: SynthParams) -> list[TrackSpec]:
    """Events placed by *received* CPA time (any number, any spacing, any clip length)."""
    times = list(p.event_rx_times_s)
    n = len(times)
    classes = list(p.event_classes) if p.event_classes else ["prop"] * n
    base_heading = rng.uniform(0, 360)
    n_curved = int(round(p.curved_fraction * n))
    curved = set(rng.choice(n, size=n_curved, replace=False).tolist()) if n_curved else set()
    specs = []
    for i, (t_rx, cls) in enumerate(zip(times, classes)):
        sp_rng, alt_rng, f_rng = _class_ranges(p, cls)
        thr = p.threshold(cls) * p.event_max_slant_frac
        for _ in range(100):
            alt = rng.uniform(*alt_rng)
            off = rng.uniform(*p.offset_range_m)
            if np.hypot(alt, off) <= thr:
                break
        else:
            alt, off = min(alt_rng[0], 0.7 * thr), 0.0
        heading = (base_heading + i * 180.0 / max(n, 2) + rng.uniform(-15, 15) + 180.0 * rng.integers(0, 2)) % 360
        specs.append(TrackSpec(
            icao24=f"{rng.integers(0, 2**24):06x}", callsign=f"EVT{i + 1:03d}", t_cpa_rel_s=None,
            heading_deg=float(heading), speed_mps=float(rng.uniform(*sp_rng)), alt_agl_m=float(alt),
            offset_m=float(off * rng.choice([-1, 1])), f0_hz=float(rng.uniform(*f_rng)),
            turn_rate_deg_s=float(rng.uniform(0.3, 0.8) * rng.choice([-1, 1])) if i in curved else 0.0,
            cls=cls, t_rx_cpa_rel_s=float(t_rx),
        ))
    return specs


def _random_spec(rng, p, cls, role, idx, anchor, t_rx, speed=None, offset_scale=1.0) -> TrackSpec:
    sp_rng, alt_rng, f_rng = _class_ranges(p, cls)
    return TrackSpec(
        icao24=f"{rng.integers(0, 2**24):06x}", callsign=f"{role[:3].upper()}{idx:03d}", t_cpa_rel_s=None,
        heading_deg=float(rng.uniform(0, 360)),
        speed_mps=float(speed if speed is not None else rng.uniform(*sp_rng)),
        alt_agl_m=float(rng.uniform(*alt_rng)), offset_m=float(rng.uniform(-1, 1) * offset_scale),
        f0_hz=float(rng.uniform(*f_rng)), cls=cls, role=role, t_rx_cpa_rel_s=float(t_rx),
        anchor_enu=(float(anchor[0]), float(anchor[1])),
    )


def place_non_events(
    rng: np.random.Generator, p: SynthParams, events: list[TrackSpec], event_enu: list[NDArray],
    truths: list[EventTruth], mic_enu: NDArray, ground_rel: float, t_rel: NDArray, c: float,
) -> tuple[list[TrackSpec], list[NDArray], list[DecoyTruth]]:
    """Decoys over each event's mirror cell, speed twins, and background traffic — all inaudible at the mic."""
    specs, enus, info = [], [], []
    far = p.inaudible_margin

    def try_place(make, role, cls, ev_idx, target=None, tries=300):
        for _ in range(tries):
            s = resolve_rx_time(make(), mic_enu, ground_rel, t_rel, c, n_iter=0)
            enu = build_track_enu(s, mic_enu, ground_rel, t_rel)
            d_mic = min_slant_in_clip(enu, t_rel, mic_enu, c, p.duration_s)
            if d_mic < far * p.threshold(cls):
                continue
            d_tgt = None
            if target is not None:
                tgt3 = np.array([target[0], target[1], mic_enu[2]])
                d_tgt = min_slant_in_clip(enu, t_rel, tgt3, c, p.duration_s)
                if d_tgt > p.decoy_target_slant_frac * p.threshold(cls):
                    continue
            specs.append(s)
            enus.append(enu)
            info.append(DecoyTruth(s.icao24, role, cls, ev_idx,
                                   None if target is None else (float(target[0]), float(target[1])), d_mic, d_tgt))
            return True
        info.append(DecoyTruth("", role, cls, ev_idx, None if target is None else tuple(map(float, target)),
                               float("nan"), None))  # placement failed; recorded so tests can see it
        return False

    def decoy_feasible(target, cls) -> bool:
        """A decoy can be audible at the target yet inaudible at the mic only if they are far enough apart."""
        sep = float(np.hypot(*(np.asarray(target) - mic_enu[:2])))
        return sep >= (far - p.decoy_target_slant_frac) * p.threshold(cls)

    clip = p.duration_s

    def far_anchor(cls):
        """Random point in the wider area (the search region plus an audible-threshold rim)."""
        R = p.region_radius_m + 2 * p.threshold(cls)
        ang, rad = rng.uniform(0, 2 * np.pi), R * np.sqrt(rng.uniform())
        return (rad * np.cos(ang), rad * np.sin(ang))

    for i, (es, enu, tru) in enumerate(zip(events, event_enu, truths)):
        mirror = mirror_point(enu, t_rel, tru.t0_rel_s, mic_enu)
        for j in range(p.n_decoys_per_event):
            cls = "prop" if rng.uniform() < 0.7 else "jet"
            if not decoy_feasible(mirror, cls):
                info.append(DecoyTruth("", "decoy", cls, i, (float(mirror[0]), float(mirror[1])), float("nan"), None))
                continue
            try_place(_decoy_maker(rng, p, mirror, mic_enu, cls, 10 * i + j), "decoy", cls, i, target=mirror)
        for j in range(p.n_twins_per_event):
            try_place(lambda: _random_spec(rng, p, es.cls, "twin", 10 * i + j, far_anchor(es.cls),
                                           tru.t_cpa_received_rel_s + rng.uniform(-30, 30),
                                           speed=es.speed_mps * rng.uniform(0.97, 1.03), offset_scale=0.0),
                      "twin", es.cls, i)
    for j in range(p.n_background):
        cls = "prop" if rng.uniform() < 0.5 else "jet"
        try_place(lambda: _random_spec(rng, p, cls, "background", j, far_anchor(cls),
                                       rng.uniform(-0.2, 1.2) * clip, offset_scale=0.0),
                  "background", cls, None)
    return specs, enus, info


def _decoy_maker(rng: np.random.Generator, p: SynthParams, target: NDArray, mic_enu: NDArray, cls: str, idx: int):
    """Spec factory: fly across the mic→target line on the far side of the target, close enough to be
    audible there while every point of the (straight) track stays beyond the mic's threshold."""
    target = np.asarray(target, float)
    sep_vec = target - mic_enu[:2]
    sep = float(np.hypot(*sep_vec))
    u = sep_vec / max(sep, 1e-9)
    thr, far = p.threshold(cls), p.inaudible_margin

    def mk():
        s_lo, s_hi = far * thr - sep, p.decoy_target_slant_frac * thr
        s_off = rng.uniform(s_lo, s_lo + 0.6 * (s_hi - s_lo))
        anchor = target + u * s_off
        sp = _random_spec(rng, p, cls, "decoy", idx, anchor, rng.uniform(0.1, 0.9) * p.duration_s)
        hdg = (np.degrees(np.arctan2(u[0], u[1])) + 90 + rng.uniform(-15, 15) + 180 * rng.integers(0, 2)) % 360
        alt_lo = _class_ranges(p, cls)[1][0]
        return replace(sp, heading_deg=float(hdg), alt_agl_m=float(rng.uniform(alt_lo, alt_lo + 200)))
    return mk


def decoy_target_feasible(p: SynthParams, target: ArrayLike, mic_enu: NDArray, cls: str = "prop") -> bool:
    """A decoy can be audible at the target yet inaudible at the mic only if they are far enough apart."""
    sep = float(np.hypot(*(np.asarray(target, float)[:2] - mic_enu[:2])))
    return sep >= (p.inaudible_margin - p.decoy_target_slant_frac) * p.threshold(cls)


def place_target_decoys(rng: np.random.Generator, p: SynthParams, targets: Sequence[tuple[float, float]],
                        mic_enu: NDArray, ground_rel: float, t_rel: NDArray, c: float,
                        tries: int = 300) -> tuple[list[TrackSpec], list[NDArray], list[DecoyTruth]]:
    """One prop decoy per target point (audible there, inaudible at the mic). Infeasible targets are recorded."""
    specs, enus, info = [], [], []
    for j, tgt in enumerate(targets):
        tgt = np.asarray(tgt, float)[:2]
        cls = "prop"
        if not decoy_target_feasible(p, tgt, mic_enu, cls):
            info.append(DecoyTruth("", "decoy", cls, None, (float(tgt[0]), float(tgt[1])), float("nan"), None))
            continue
        mk = _decoy_maker(rng, p, tgt, mic_enu, cls, 900 + j)
        tgt3 = np.array([tgt[0], tgt[1], mic_enu[2]])
        for _ in range(tries):
            s = resolve_rx_time(mk(), mic_enu, ground_rel, t_rel, c, n_iter=0)
            enu = build_track_enu(s, mic_enu, ground_rel, t_rel)
            d_mic = min_slant_in_clip(enu, t_rel, mic_enu, c, p.duration_s)
            d_tgt = min_slant_in_clip(enu, t_rel, tgt3, c, p.duration_s)
            if d_mic >= p.inaudible_margin * p.threshold(cls) and d_tgt <= p.decoy_target_slant_frac * p.threshold(cls):
                specs.append(s)
                enus.append(enu)
                info.append(DecoyTruth(s.icao24, "decoy", cls, None, (float(tgt[0]), float(tgt[1])), d_mic, d_tgt))
                break
        else:
            info.append(DecoyTruth("", "decoy", cls, None, (float(tgt[0]), float(tgt[1])), float("nan"), None))
    return specs, enus, info


def assign_types(rng: np.random.Generator, specs: list[TrackSpec]) -> list[TrackSpec]:
    out = []
    for s in specs:
        if not s.type_code:
            table = JET_TYPES if s.cls == "jet" else PROP_TYPES
            s = replace(s, type_code=str(rng.choice(sorted(table))))
        out.append(s)
    return out


def synthetic_registry(rng: np.random.Generator, specs: list[TrackSpec], unknown_frac: float) -> pd.DataFrame:
    """OpenSky-style registry rows (icao24, typecode, icaoaircrafttype); some aircraft left out."""
    rows = []
    for s in specs:
        if rng.uniform() < unknown_frac:
            continue
        desc = {**PROP_TYPES, **JET_TYPES}.get(s.type_code, "")
        rows.append({"icao24": s.icao24, "typecode": s.type_code, "icaoaircrafttype": desc})
    return pd.DataFrame(rows, columns=["icao24", "typecode", "icaoaircrafttype"])


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


def tone_power_reference(spec: TrackSpec, truth_d_m: float, noise_band_power: float, p: SynthParams) -> tuple[float, float]:
    """(tone power at r_ref, r_ref). Events with an SNR use their CPA as reference; others a class level."""
    if spec.role == "event":
        snr = p.snr_db if spec.snr_db is None else spec.snr_db
        return noise_band_power * 10 ** (snr / 10), truth_d_m
    lvl = p.jet_snr_at_ref_db if spec.cls == "jet" else p.prop_snr_at_ref_db
    return noise_band_power * 10 ** (lvl / 10), p.ref_distance_m


def flyover_audio(
    rng: np.random.Generator, spec: TrackSpec, truth: EventTruth, t_track_rel: NDArray, enu: NDArray,
    mic_enu: NDArray, t_audio_rel: NDArray, fs: float, noise_band_power: float, p: SynthParams, c: float,
) -> NDArray:
    """One aircraft at the mic: delayed, Doppler-shifted tones (+ broadband), 1/r + excess attenuation."""
    r = np.linalg.norm(enu - mic_enu, axis=1)
    t_r = t_track_rel + r / c
    jitter = gaussian_filter1d(rng.standard_normal(t_track_rel.size), sigma=20.0 / p.track_dt_s)
    jitter *= p.f0_jitter_frac / max(np.std(jitter), 1e-12)
    phase_e = 2 * np.pi * spec.f0_hz * np.concatenate(
        [[0.0], np.cumsum(0.5 * ((1 + jitter[1:]) + (1 + jitter[:-1])) * np.diff(t_track_rel))]
    )
    valid = (t_audio_rel >= t_r[0]) & (t_audio_rel <= t_r[-1])
    tq = t_audio_rel[valid]
    phase = CubicSpline(t_r, phase_e)(tq)
    rq = np.interp(tq, t_r, r)

    is_jet = spec.cls == "jet"
    amps = np.asarray(p.jet_harmonic_amps if is_jet else p.harmonic_amps, float)
    p_ref, r_ref = tone_power_reference(spec, truth.d_m, noise_band_power, p)
    a_ref = np.sqrt(2 * p_ref / np.sum(amps**2))
    gain = (r_ref / rq) * 10 ** (-p.excess_atten_db_per_km * (rq - r_ref) / 1000 / 20)

    sig = np.zeros_like(tq)
    for k, a in enumerate(amps, start=1):
        sig += a * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
    out = np.zeros_like(t_audio_rel)
    out[valid] = a_ref * gain * sig
    if is_jet:
        # Broadband jet noise, Doppler-warped: sample a source noise process at emission time.
        te_q = CubicSpline(t_r, t_track_rel)(tq)
        n_src = int(np.ceil((t_track_rel[-1] - t_track_rel[0]) * fs)) + 2
        src = bandlimited_noise(rng, n_src, fs, p.jet_broadband_band_hz)
        bb = np.interp((te_q - t_track_rel[0]) * fs, np.arange(n_src), src)
        out[valid] += np.sqrt(p_ref * 10 ** (p.jet_broadband_to_tone_db / 10)) * gain * bb
    elif p.broadband_to_tone_db > -np.inf:
        bb = bandlimited_noise(rng, t_audio_rel.size, fs, p.noise_band_hz)
        out[valid] += np.sqrt(p_ref * 10 ** (p.broadband_to_tone_db / 10)) * gain * bb[valid]
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
    decoy_targets_enu: Sequence[tuple[float, float]] = (),
) -> Scenario:
    """Build tracks, a hidden mic and the recording. Deterministic given ``params.seed``.

    With ``specs`` given they are used as-is (roles from each spec). Otherwise
    events come from ``event_rx_times_s`` (or the legacy even spread), and
    decoys / twins / background are added per the params.
    """
    p = params
    rng = np.random.default_rng(p.seed)
    aux = np.random.default_rng([p.seed, 1])  # non-event placement and types: keeps the event stream stable
    rad = p.mic_max_offset_m * np.sqrt(rng.uniform())   # always drawn, so a given mic_enu keeps the event stream
    ang = rng.uniform(0, 2 * np.pi)
    if mic_enu is None:
        mic_enu = np.array([rad * np.cos(ang), rad * np.sin(ang), 0.0])
    mic_enu = np.array([mic_enu[0], mic_enu[1], ground_elev_m + p.mic_height_m - frame.h0], float)
    auto = specs is None
    if auto:
        specs = random_event_specs(rng, p) if p.event_rx_times_s is not None else random_track_specs(rng, p)

    fs = p.fs_hz
    n = int(round(p.duration_s * fs))
    t_audio = np.arange(n) / fs
    t_track = np.arange(-p.track_pad_before_s, p.duration_s + p.track_pad_after_s + 1e-9, p.track_dt_s)
    ground_rel = ground_elev_m - frame.h0

    specs = [resolve_rx_time(s, mic_enu, ground_rel, t_track, c) for s in specs]
    ev_specs = [s for s in specs if s.role == "event"]
    ev_enu = [build_track_enu(s, mic_enu, ground_rel, t_track) for s in ev_specs]
    truths = [event_truth(s.icao24, s.f0_hz, t_track, e, mic_enu, c, t_near_s=s.t_cpa_rel_s, cls=s.cls)
              for s, e in zip(ev_specs, ev_enu)]
    decoys: list[DecoyTruth] = []
    if auto:
        extra, _, decoys = place_non_events(aux, p, ev_specs, ev_enu, truths, mic_enu, ground_rel, t_track, c)
        specs = specs + extra
    specs = assign_types(aux, specs)
    if len(decoy_targets_enu):
        drng = np.random.default_rng([p.seed, 2])
        extra, _, more = place_target_decoys(drng, p, decoy_targets_enu, mic_enu, ground_rel, t_track, c)
        specs = specs + assign_types(drng, extra)
        decoys = decoys + more

    noise = colored_noise(rng, n, fs)
    p_noise = band_power(noise, fs, p.noise_band_hz)
    audio = noise.copy()
    tracks, enus = [], []
    ev_iter = iter(truths)
    for s in specs:
        enu = build_track_enu(s, mic_enu, ground_rel, t_track)
        if s.role == "event":
            tru = next(ev_iter)
        else:
            tru = event_truth(s.icao24, s.f0_hz, t_track, enu, mic_enu, c, cls=s.cls)
        if s.role == "event" or min_slant_in_clip(enu, t_track, mic_enu, c, p.duration_s) < p.skip_audio_beyond_m:
            audio += flyover_audio(rng, s, tru, t_track, enu, mic_enu, t_audio, fs, p_noise, p, c)
        tr = Track.from_enu(s.icao24, s.callsign, start_time_s + t_track, enu, frame, type_code=s.type_code)
        # velocity in each fix's own local ENU (as ADS-B reports it), not the scenario frame's
        v_ecef = enu_vector_to_ecef(np.gradient(enu, t_track, axis=0), frame.lat0, frame.lon0)
        tr.vel_enu = np.einsum("nij,nj->ni", enu_basis(tr.lat_deg, tr.lon_deg), v_ecef)
        tracks.append(tr)
        enus.append(enu)
    audio = (0.9 * audio / np.max(np.abs(audio))).astype(np.float32)
    lat, lon, _ = frame.to_geodetic(mic_enu)
    return Scenario(
        frame=frame, start_time_s=start_time_s, fs_hz=fs, audio=audio, tracks=tracks, track_enu=enus,
        mic_enu=mic_enu, mic_lat=float(lat), mic_lon=float(lon), ground_elev_m=ground_elev_m,
        specs=specs, truth=truths, c=c, params=p, decoys=decoys,
        registry=synthetic_registry(aux, specs, p.unknown_type_frac),
    )
