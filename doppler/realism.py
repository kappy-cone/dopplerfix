"""Realism layer for synthetic scenarios: what real recordings and real ADS-B data do that a clean forward
model doesn't. Every effect is a toggle in :class:`RealismParams`; with all toggles off nothing here runs,
so the clean scenarios (and their tests) are unchanged. Randomness comes from separate, seeded streams.

Grouped by where it acts:
  * ADS-B:   irregular 1 s fixes, per-track timestamp bias (latency), position noise and MLAT fixes,
             25 ft altitude steps, baro-only aircraft with a baro−geometric offset, 1 kt / 64 fpm steps,
             coverage gaps, dropped aircraft.            -> :func:`degrade_adsb`
  * Source:  shared per-aircraft level distribution, rich harmonic stacks with a half-order comb,
             RPM random walk and ramps, twin-engine detuning, directivity.   -> :func:`draw_source`
  * Path:    ISO 9613-1 atmospheric absorption per harmonic, ground reflection (image source), wind
             (effective sound speed along the ray), speed-of-sound mismatch with the pipeline.
  * Scene:   non-stationary noise (wind gusts, mains hum, car passes, bird chirps), recording chain
             (high-pass, AGC), clock offset, random mic height.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.ndimage import gaussian_filter1d
from scipy.signal import butter, sosfilt

FT, KT, FPM = 0.3048, 0.514444, 0.00508


@dataclass(frozen=True)
class RealismParams:
    # --- ADS-B --------------------------------------------------------------------------------------
    adsb_degrade: bool = False
    adsb_interval_s: float = 1.0            # mean fix interval (OpenSky-like)
    adsb_interval_jitter_s: float = 0.3
    adsb_latency_s: float = 0.6             # per-track timestamp bias ~ U(±latency) (DO-260B allowance)
    adsb_pos_sigma_m: float = 7.0
    adsb_mlat_frac: float = 0.03            # fraction of fixes from multilateration
    adsb_mlat_sigma_m: float = 100.0
    adsb_geo_alt_sigma_m: float = 10.0
    adsb_alt_quant_ft: float = 25.0
    adsb_baro_only_frac_prop: float = 0.3   # GA without geometric altitude
    adsb_baro_offset_sigma_m: float = 60.0  # baro − geometric (pressure / temperature)
    adsb_speed_quant_kt: float = 1.0
    adsb_vrate_quant_fpm: float = 64.0
    adsb_gap_rate_per_min: float = 0.2      # coverage gaps (Poisson)
    adsb_gap_s: tuple[float, float] = (5.0, 60.0)
    adsb_drop_prop_frac: float = 0.0        # non-event props missing from ADS-B entirely (UAT / no coverage)
    # --- source -------------------------------------------------------------------------------------
    level_model: str = "legacy"             # legacy | shared (one level distribution for every aircraft)
    level_sigma_db: float = 6.0
    event_min_snr_db: float = 0.0           # shared: event's band SNR at CPA must reach this (it was heard)
    harmonic_model: str = "legacy"          # legacy | rich
    n_harmonics: int = 10
    harmonic_slope_db: float = -3.0         # per order
    harmonic_scatter_db: float = 6.0
    half_order_db: float = -12.0            # piston half-order comb relative to the stack (rich, props)
    rpm_walk_frac: float = 0.0              # std of the RPM random walk over a pass
    rpm_ramp_prob: float = 0.0              # fraction of aircraft with a power change during the pass
    rpm_ramp_frac: tuple[float, float] = (0.03, 0.10)
    twin_prob: float = 0.0                  # twin-engine props: second tone set detuned
    twin_detune_frac: tuple[float, float] = (0.002, 0.01)
    directivity_db: float = 0.0             # prop: gain ±dB·cosθ; jets: fan tone fades aft by 2× this
    # --- path ---------------------------------------------------------------------------------------
    absorption: str = "flat"                # flat (synth.excess_atten_db_per_km) | iso9613
    humidity_pct: float = 70.0
    ground_reflection: bool = False
    reflection_coef: tuple[float, float] = (0.8, 1.0)
    wind_max_mps: float = 0.0               # uniform wind, random direction, speed U(0, max)
    c_mismatch_mps: float = 0.0             # true c = pipeline c + U(±this) (≈ ±5 °C for 3 m/s)
    # --- scene / recording --------------------------------------------------------------------------
    noise_model: str = "stationary"         # stationary | realistic
    wind_noise_db: float = 6.0              # gust noise (< 200 Hz) relative to the background band power
    hum_db: float = -10.0                   # 60 Hz comb
    n_car_passes: int = 0
    car_db: float = 10.0
    n_bird_chirps: int = 0
    bird_db: float = 0.0
    highpass_hz: float = 0.0                # recording-chain high-pass (0 = off)
    agc_strength: float = 0.0               # 0 = off, 1 = full level riding
    clock_offset_max_s: float = 0.0         # stated start = true start + U(±this)
    # --- kinematics / geometry ----------------------------------------------------------------------
    corridor_frac: float = 0.0              # events after the first follow its heading (±corridor_spread, or reciprocal)
    corridor_spread_deg: float = 20.0
    climb_prob: float = 0.0                 # props climbing / descending through the pass
    climb_mps: tuple[float, float] = (3.0, 5.0)
    speed_ramp_prob: float = 0.0
    speed_ramp_frac: float = 0.10           # up to ±this fractional speed change across the pass
    turn_rate_max_deg_s: float = 0.8        # curved events: |turn rate| up to this (3 = standard rate)
    n_near_traffic: int = 0                 # extra aircraft whose closest in-clip slant is 0.5–1.5× the threshold
    mic_height_range_m: tuple[float, float] | None = None   # true mic height (pipeline assumes mic.height_agl_m)

    @classmethod
    def from_dict(cls, d: dict) -> "RealismParams":
        kw = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        for k in ("adsb_gap_s", "rpm_ramp_frac", "twin_detune_frac", "reflection_coef", "mic_height_range_m",
                  "climb_mps"):
            if kw.get(k) is not None:
                kw[k] = tuple(kw[k])
        return cls(**kw)

    @property
    def any_on(self) -> bool:
        return self != RealismParams()


def perturb_kinematics(rng: np.random.Generator, specs: list, p: RealismParams) -> list:
    """Corridor-aligned events, climbs/descents, speed ramps and harder turns (realistic traffic)."""
    from dataclasses import replace

    out, h0 = [], None
    for s in specs:
        kw = {}
        if s.role == "event":
            if h0 is None:
                h0 = s.heading_deg
            elif rng.uniform() < p.corridor_frac:
                kw["heading_deg"] = float((h0 + rng.uniform(-p.corridor_spread_deg, p.corridor_spread_deg)
                                           + 180.0 * rng.integers(0, 2)) % 360)
            if s.turn_rate_deg_s:
                kw["turn_rate_deg_s"] = float(np.sign(s.turn_rate_deg_s) * rng.uniform(0.3, p.turn_rate_max_deg_s))
                kw["turn_half_s"] = float(rng.uniform(10.0, 60.0))
        if s.cls != "jet" and rng.uniform() < p.climb_prob:
            kw["climb_mps"] = float(rng.uniform(*p.climb_mps) * rng.choice([-1, 1]))
        if rng.uniform() < p.speed_ramp_prob:
            kw["accel_frac"] = float(rng.uniform(-p.speed_ramp_frac, p.speed_ramp_frac))
        out.append(replace(s, **kw) if kw else s)
    return out


# ---------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------
def iso9613_alpha_db_per_m(f_hz: NDArray, temp_c: float = 20.0, rh_pct: float = 70.0,
                           pa_kpa: float = 101.325) -> NDArray:
    """ISO 9613-1 pure-tone atmospheric absorption coefficient (dB/m)."""
    f = np.asarray(f_hz, float)
    T, T0, T01, pr = temp_c + 273.15, 293.15, 273.16, 101.325
    pa = pa_kpa / pr
    C = -6.8346 * (T01 / T) ** 1.261 + 4.6151
    h = rh_pct * (10 ** C) / pa
    frO = pa * (24 + 4.04e4 * h * (0.02 + h) / (0.391 + h))
    frN = pa * (T / T0) ** -0.5 * (9 + 280 * h * np.exp(-4.170 * ((T / T0) ** (-1 / 3) - 1)))
    return 8.686 * f ** 2 * (1.84e-11 / pa * (T / T0) ** 0.5 + (T / T0) ** -2.5 * (
        0.01275 * np.exp(-2239.1 / T) / (frO + f ** 2 / frO) + 0.1068 * np.exp(-3352.0 / T) / (frN + f ** 2 / frN)))


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------
@dataclass
class SourceRealism:
    """Per-aircraft draws (one per spec)."""

    level_offset_db: float = 0.0            # shared level model: offset from the class mean
    harmonics: NDArray = field(default_factory=lambda: np.array([1.0]))   # amplitude per order 1..n
    half_order: NDArray | None = None       # amplitudes at (k − 0.5)·f0, k = 1..n
    rpm_walk: float = 0.0
    ramp_frac: float = 0.0                  # total fractional RPM change across the ramp
    ramp_t_rel_s: float = 0.0               # ramp centre (recording-relative emission time)
    ramp_dur_s: float = 30.0
    twin_detune: float = 0.0
    directivity_db: float = 0.0             # prop: ±dB·cosθ
    reflection: float = 0.0


def draw_source(rng: np.random.Generator, cls: str, p: RealismParams, base_amps: NDArray,
                duration_s: float) -> SourceRealism:
    s = SourceRealism(harmonics=np.asarray(base_amps, float))
    if p.level_model == "shared":
        s.level_offset_db = float(rng.normal(0, p.level_sigma_db))
    if p.harmonic_model == "rich":
        n = p.n_harmonics if cls != "jet" else max(2, p.n_harmonics // 4)
        k = np.arange(1, n + 1)
        db = p.harmonic_slope_db * (k - 1) + rng.normal(0, p.harmonic_scatter_db, n)
        s.harmonics = 10 ** (db / 20)
        if cls != "jet":
            s.half_order = 10 ** ((db + p.half_order_db + rng.normal(0, 3, n)) / 20)
    s.rpm_walk = p.rpm_walk_frac
    if rng.uniform() < p.rpm_ramp_prob:
        s.ramp_frac = float(rng.uniform(*p.rpm_ramp_frac) * rng.choice([-1, 1]))
        s.ramp_t_rel_s = float(rng.uniform(0, duration_s))
        s.ramp_dur_s = float(rng.uniform(10, 40))
    if cls != "jet" and rng.uniform() < p.twin_prob:
        s.twin_detune = float(rng.uniform(*p.twin_detune_frac))
    s.directivity_db = float(rng.uniform(-1, 1) * p.directivity_db)
    if p.ground_reflection:
        s.reflection = float(rng.uniform(*p.reflection_coef))
    return s


def rpm_profile(rng: np.random.Generator, t_rel: NDArray, s: SourceRealism, dt_s: float) -> NDArray:
    """Fractional RPM deviation over the track's time axis (random walk + optional smooth ramp)."""
    out = np.zeros_like(t_rel)
    if s.rpm_walk > 0:
        w = np.cumsum(rng.standard_normal(t_rel.size))
        w = gaussian_filter1d(w - w.mean(), sigma=2.0 / dt_s)
        out += s.rpm_walk * w / max(np.std(w), 1e-12)
    if s.ramp_frac:
        out += s.ramp_frac * 0.5 * (1 + np.tanh((t_rel - s.ramp_t_rel_s) / (0.25 * s.ramp_dur_s)))
    return out


def directivity_gain(cos_theta: NDArray, cls: str, s: SourceRealism, p: RealismParams) -> NDArray:
    """cosθ = cos(angle between aircraft velocity and the ray to the mic): +1 ahead, −1 behind."""
    if p.directivity_db <= 0:
        return np.ones_like(cos_theta)
    if cls == "jet":   # fan tones radiate forward: up to −2·directivity_db aft
        return 10 ** (-p.directivity_db * (1 - cos_theta) / 20)
    return 10 ** (s.directivity_db * cos_theta / 20)


# ---------------------------------------------------------------------------
# Scene noise and recording chain
# ---------------------------------------------------------------------------
def scene_noise(rng: np.random.Generator, n: int, fs: float, band_power: float, p: RealismParams,
                lowband) -> NDArray:
    """Non-stationary additions scaled to the background's band power: gusts, hum, cars, birds."""
    t = np.arange(n) / fs
    out = np.zeros(n)
    if p.noise_model != "realistic":
        return out
    ref = np.sqrt(band_power)
    # wind gusts: low-passed noise with a log-normal envelope
    g = lowband(rng, n, fs, (20.0, 200.0))
    env = np.exp(gaussian_filter1d(rng.standard_normal(n // 800 + 2), 3.0))
    env = np.interp(np.arange(n), np.linspace(0, n - 1, env.size), env)
    out += ref * 10 ** (p.wind_noise_db / 20) * g * env / np.sqrt(np.mean(env ** 2))
    # mains hum comb
    for k, a in enumerate((1.0, 0.5, 0.3, 0.2, 0.1), start=1):
        out += ref * 10 ** (p.hum_db / 20) * a * np.sqrt(2) * np.sin(2 * np.pi * 60 * k * t + rng.uniform(0, 6.3))
    # car passes: broadband 1/r² burst + Doppler-shifted engine tone (fast transitions)
    for _ in range(p.n_car_passes):
        tc, v, d = rng.uniform(0, t[-1]), rng.uniform(10, 30), rng.uniform(10, 60)
        r = np.sqrt(d ** 2 + (v * (t - tc)) ** 2)
        amp = ref * 10 ** (p.car_db / 20) * (d / r)
        out += amp * lowband(rng, n, fs, (60.0, 1500.0))
        f0 = rng.uniform(30, 90)
        rdot = v ** 2 * (t - tc) / r
        ph = 2 * np.pi * np.cumsum(f0 * 343.0 / (343.0 + rdot)) / fs
        out += 0.5 * amp * np.sin(ph)
    # bird chirps: short FM sweeps 2–6 kHz (inside the jet band)
    for _ in range(p.n_bird_chirps):
        t0, dur = rng.uniform(0, t[-1]), rng.uniform(0.15, 0.5)
        f_a, f_b = rng.uniform(2000, 6000, 2)
        m = (t >= t0) & (t < t0 + dur)
        if not m.any() or f_b >= fs / 2 or f_a >= fs / 2:
            continue
        u = (t[m] - t0) / dur
        ph = 2 * np.pi * (f_a * (t[m] - t0) + 0.5 * (f_b - f_a) * dur * u ** 2)
        out[m] += ref * 10 ** (p.bird_db / 20) * np.sqrt(2) * np.sin(ph) * np.sin(np.pi * u)
    return out


def recording_chain(x: NDArray, fs: float, p: RealismParams) -> NDArray:
    """Device high-pass and slow AGC (level riding)."""
    if p.highpass_hz > 0:
        x = sosfilt(butter(2, p.highpass_hz, "highpass", fs=fs, output="sos"), x)
    if p.agc_strength > 0:   # block RMS every 0.1 s, smoothed over ~2 s, interpolated back (cheap)
        blk = max(1, int(0.1 * fs))
        nb = len(x) // blk
        rms = np.sqrt(np.mean(x[:nb * blk].reshape(nb, blk) ** 2, axis=1) + 1e-12)
        env = np.sqrt(gaussian_filter1d(rms ** 2, 10.0, mode="nearest"))
        gain = (np.median(env) / env) ** p.agc_strength
        x = x * np.interp(np.arange(len(x)), (np.arange(nb) + 0.5) * blk, gain)
    return x


# ---------------------------------------------------------------------------
# ADS-B
# ---------------------------------------------------------------------------
def degrade_adsb(rng: np.random.Generator, df: pd.DataFrame, cls_by_icao: dict[str, str], p: RealismParams,
                 protect: set[str] = frozenset()) -> tuple[pd.DataFrame, dict]:
    """Clean canonical-schema track table -> what a receiver network reports. Returns (table, per-track info).

    ``protect``: icao24s never dropped (the scenario's events), so a coverage loss is a deliberate stress
    setting (adsb_drop_prop_frac) rather than an accident.
    """
    if not p.adsb_degrade:
        return df, {}
    t_all = pd.to_datetime(df["time_utc"], utc=True)
    df = df.assign(_t=(t_all - pd.Timestamp(0, tz="UTC")).dt.total_seconds())
    out, info = [], {}
    for icao, g in df.groupby("icao24", sort=False):
        cls = cls_by_icao.get(icao, "prop")
        if cls != "jet" and icao not in protect and rng.uniform() < p.adsb_drop_prop_frac:
            info[icao] = {"dropped": True}
            continue
        g = g.sort_values("_t")
        t0, t1 = float(g["_t"].iloc[0]), float(g["_t"].iloc[-1])
        n_max = int((t1 - t0) / max(p.adsb_interval_s - p.adsb_interval_jitter_s, 0.2)) + 2
        steps = np.clip(rng.normal(p.adsb_interval_s, p.adsb_interval_jitter_s, n_max), 0.2, None)
        tf = t0 + np.cumsum(steps)
        tf = tf[tf <= t1]
        keep = np.ones(tf.size, bool)
        n_gaps = rng.poisson(p.adsb_gap_rate_per_min * (t1 - t0) / 60.0)
        gaps = []
        for _ in range(n_gaps):
            a = rng.uniform(t0, t1)
            b = a + rng.uniform(*p.adsb_gap_s)
            keep &= ~((tf >= a) & (tf < b))
            gaps.append((a, b))
        tf = tf[keep]
        if tf.size < 2:
            info[icao] = {"dropped": True}
            continue
        col = {c: np.interp(tf, g["_t"].to_numpy(), g[c].to_numpy(float))
               for c in ("lat", "lon", "geometric_alt_m", "ground_speed_mps", "vertical_rate_mps")}
        trk = np.unwrap(np.deg2rad(g["track_deg"].to_numpy(float)))
        col["track_deg"] = np.rad2deg(np.interp(tf, g["_t"].to_numpy(), trk)) % 360
        sig = np.where(rng.uniform(size=tf.size) < p.adsb_mlat_frac, p.adsb_mlat_sigma_m, p.adsb_pos_sigma_m)
        lat = col["lat"] + rng.normal(0, 1, tf.size) * sig / 111_320.0
        lon = col["lon"] + rng.normal(0, 1, tf.size) * sig / (111_320.0 * np.cos(np.deg2rad(col["lat"])))
        q = p.adsb_alt_quant_ft * FT
        geo = np.round((col["geometric_alt_m"] + rng.normal(0, p.adsb_geo_alt_sigma_m, tf.size)) / q) * q
        baro_off = float(rng.normal(0, p.adsb_baro_offset_sigma_m))
        baro = np.round((col["geometric_alt_m"] - baro_off) / q) * q
        baro_only = cls != "jet" and rng.uniform() < p.adsb_baro_only_frac_prop
        if baro_only:
            geo = np.full(tf.size, np.nan)
        bias = float(rng.uniform(-p.adsb_latency_s, p.adsb_latency_s))
        gs = np.round(col["ground_speed_mps"] / (p.adsb_speed_quant_kt * KT)) * p.adsb_speed_quant_kt * KT
        vz = np.round(col["vertical_rate_mps"] / (p.adsb_vrate_quant_fpm * FPM)) * p.adsb_vrate_quant_fpm * FPM
        out.append(pd.DataFrame({
            "time_utc": pd.to_datetime(tf + bias, unit="s", utc=True), "icao24": icao,
            "callsign": g["callsign"].iloc[0], "lat": lat, "lon": lon, "geometric_alt_m": geo, "baro_alt_m": baro,
            "ground_speed_mps": gs, "track_deg": np.round(col["track_deg"], 1), "vertical_rate_mps": vz}))
        info[icao] = {"dropped": False, "time_bias_s": bias, "baro_only": bool(baro_only),
                      "baro_offset_m": baro_off, "n_fixes": int(tf.size), "gaps": gaps}
    cols = [c for c in df.columns if c != "_t"]
    res = pd.concat(out, ignore_index=True)[cols] if out else df.iloc[:0][cols]
    return res, info
