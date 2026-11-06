"""ADS-B tracks: normalisation, cleaning, interpolation and the vectorised TrackSet.

Canonical tabular schema (CSV/Parquet), one row per fix:
    time_utc | icao24 | callsign | lat | lon | geometric_alt_m | baro_alt_m |
    ground_speed_mps | track_deg | vertical_rate_mps
OpenSky-style names (time, geoaltitude, baroaltitude, velocity, heading,
vertrate) are mapped automatically; other names can be mapped via config.

OpenSky state vectors (the scientific datasets' hourly CSV, and the Trino table
``state_vectors_data4``, which share one schema; docs/opensky_ingest.md) are
recognised by their time/lastposupdate/lastcontact columns and typed by
``parse_opensky_states`` (SI units already: m, m/s, deg). pyopensky's Trino
``query()``/``history()`` keep these raw names. Frames renamed by the traffic
library (``altitude``, ``groundspeed``, ``vertical_rate``; it converts to ft, kt,
ft/min) are refused unless mapped explicitly.

Heights are ellipsoidal (ADS-B geometric altitude). Barometric altitude is a
fallback, flagged per track so later stages can widen tolerances.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numpy.typing import NDArray
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import median_filter

from .aircraft_types import UNKNOWN
from .geo import LocalFrame, ecef_to_geodetic, enu_basis, enu_vector_to_ecef, geodetic_to_ecef, velocity_enu_from_adsb

TRACK_COLUMNS = [
    "time_utc", "icao24", "callsign", "lat", "lon", "geometric_alt_m", "baro_alt_m",
    "ground_speed_mps", "track_deg", "vertical_rate_mps",
]
DEFAULT_ALIASES = {
    "time": "time_utc", "timestamp": "time_utc", "latitude": "lat", "longitude": "lon",
    "geoaltitude": "geometric_alt_m", "baroaltitude": "baro_alt_m", "velocity": "ground_speed_mps",
    "heading": "track_deg", "vertrate": "vertical_rate_mps",
}
# OpenSky state-vector columns as documented (README of the state-vector samples; Trino state_vectors_data4).
OPENSKY_STATE_COLUMNS = (
    "time", "icao24", "lat", "lon", "velocity", "heading", "vertrate", "callsign", "onground", "alert",
    "spi", "squawk", "baroaltitude", "geoaltitude", "lastposupdate", "lastcontact",
)
_OPENSKY_NUMERIC = ("lat", "lon", "velocity", "heading", "vertrate", "baroaltitude", "geoaltitude",
                    "lastposupdate", "lastcontact")
_OPENSKY_FLAGS = ("onground", "alert", "spi")
# Names used by the traffic library, which renames OpenSky columns and converts to ft, kt and ft/min: their
# units are not stated by the name. Refused unless the caller maps them explicitly.
UNIT_AMBIGUOUS_COLUMNS = ("altitude", "groundspeed", "vertical_rate")


@dataclass(frozen=True)
class TrackParams:
    resample_s: float = 0.25
    max_gap_s: float = 20.0          # split (never interpolate) across longer gaps
    max_speed_mps: float = 350.0     # implied-speed outlier test
    outlier_m: float = 300.0         # horizontal deviation from neighbours' interpolation
    alt_outlier_m: float = 150.0     # deviation from a running median of altitude
    min_points: int = 5
    min_geometric_frac: float = 0.5  # below this, fall back to barometric altitude
    velocity_source: str = "adsb"    # adsb | derivative

    @classmethod
    def from_dict(cls, d: dict) -> "TrackParams":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Track:
    """One aircraft (or one gap-free segment of it). Times are epoch seconds."""

    icao24: str
    callsign: str
    t_s: NDArray
    lat_deg: NDArray
    lon_deg: NDArray
    alt_m: NDArray                     # ellipsoidal (geometric) unless alt_source == "baro"
    vel_enu: NDArray | None = None     # (N, 3) reported velocity, if any
    alt_source: str = "geometric"
    type_code: str = ""
    cls: int = UNKNOWN

    def ecef(self) -> NDArray:
        return geodetic_to_ecef(self.lat_deg, self.lon_deg, self.alt_m)

    def vel_ecef(self) -> NDArray:
        if self.vel_enu is None:
            return np.gradient(self.ecef(), self.t_s, axis=0)
        return enu_vector_to_ecef(self.vel_enu, self.lat_deg, self.lon_deg)

    def enu(self, frame: LocalFrame) -> NDArray:
        return frame.to_enu(self.lat_deg, self.lon_deg, self.alt_m)

    @classmethod
    def from_enu(klass, icao24: str, callsign: str, t_s: NDArray, enu: NDArray, frame: LocalFrame,
                 **kw) -> "Track":
        lat, lon, h = frame.to_geodetic(enu)
        return klass(icao24, callsign, np.asarray(t_s, float), lat, lon, h, **kw)


# ---------------------------------------------------------------------------
# Tabular I/O helpers (pure: DataFrame in/out)
# ---------------------------------------------------------------------------
def ground_kinematics(t_s: NDArray, enu: NDArray) -> tuple[NDArray, NDArray, NDArray]:
    """Ground speed (m/s), track (deg from north) and vertical rate by finite differences."""
    vel = np.gradient(enu, t_s, axis=0)
    gs = np.hypot(vel[:, 0], vel[:, 1])
    trk = np.degrees(np.arctan2(vel[:, 0], vel[:, 1])) % 360.0
    return gs, trk, vel[:, 2]


def tracks_to_frame(tracks: list[Track], frame: LocalFrame) -> pd.DataFrame:
    """Flatten tracks to the canonical schema (velocity from vel_enu or finite differences)."""
    rows = []
    for tr in tracks:
        if tr.vel_enu is not None:
            v = tr.vel_enu
            gs, trk, vz = np.hypot(v[:, 0], v[:, 1]), np.degrees(np.arctan2(v[:, 0], v[:, 1])) % 360, v[:, 2]
        else:
            gs, trk, vz = ground_kinematics(tr.t_s, tr.enu(frame))
        geo = tr.alt_m if tr.alt_source == "geometric" else np.full(len(tr.t_s), np.nan)
        baro = tr.alt_m if tr.alt_source == "baro" else np.full(len(tr.t_s), np.nan)
        rows.append(pd.DataFrame({
            "time_utc": pd.to_datetime(tr.t_s, unit="s", utc=True), "icao24": tr.icao24,
            "callsign": tr.callsign, "lat": tr.lat_deg, "lon": tr.lon_deg, "geometric_alt_m": geo,
            "baro_alt_m": baro, "ground_speed_mps": gs, "track_deg": trk, "vertical_rate_mps": vz,
        }))
    return pd.concat(rows, ignore_index=True)[TRACK_COLUMNS]


def is_opensky_states(df: pd.DataFrame) -> bool:
    """True for a raw OpenSky state-vector table (before renaming): has ``time`` and ``lastposupdate``.

    ``lastcontact`` is not required, so a query cropped without it is still recognised.
    """
    return {"time", "lastposupdate"} <= {str(c).lower() for c in df.columns}


def _flag(s: pd.Series) -> pd.Series:
    """OpenSky boolean flags as read from CSV ('true'/'false', 1/0, blank) -> bool (blank or null = False)."""
    if pd.api.types.is_bool_dtype(s):                     # incl. nullable / Arrow booleans with nulls
        return s.fillna(False).astype(bool)
    return s.astype(str).str.strip().str.lower().isin(["true", "t", "1", "1.0"])


_MAX_EPOCH_S = 1e11   # ~year 5100: larger "seconds" are milliseconds or nanoseconds


def _to_epoch_s(s: pd.Series, name: str) -> pd.Series:
    """Epoch seconds from numbers, datetimes or ISO-8601 strings (naive = UTC). Raises if unparseable
    or if numbers look like ms/ns, rather than letting a wrong time through as NaN or 1.6e18 s."""
    if pd.api.types.is_datetime64_any_dtype(s):
        ts = pd.to_datetime(s, utc=True)
    else:
        num = pd.to_numeric(s, errors="coerce")
        if num.notna().sum() == s.notna().sum():
            num = num.astype(float)
            if (num.abs() > _MAX_EPOCH_S).any():
                raise ValueError(f"OpenSky column {name!r}: values up to {num.abs().max():.3g} are not epoch "
                                 "seconds (milliseconds or nanoseconds?)")
            return num
        ts = pd.to_datetime(s, utc=True, format="ISO8601")
    return (ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds()


def _squawk(s: pd.Series) -> pd.Series:
    """Squawk as a 4-digit string. CSV readers turn '0123' into 123 (or 123.0 next to blanks)."""
    def one(x) -> str:
        if pd.isna(x):
            return ""
        if isinstance(x, (int, float, np.integer, np.floating)):
            return f"{int(x):04d}"
        x = str(x).strip()
        return x.zfill(4) if x.isdigit() else x
    return s.map(one).astype(object)


def parse_opensky_states(df: pd.DataFrame) -> pd.DataFrame:
    """Type a raw OpenSky state-vector table (CSV, Avro or Trino ``state_vectors_data4``).

    Units are already SI (m, m/s, deg from north; times epoch seconds), so values are not converted:
    blanks become NaN, flags become bool, squawk stays a 4-digit string. Columns keep their OpenSky
    names; ``normalize_frame`` maps them. Raises ValueError if the position columns are missing.
    """
    out = df.rename(columns=lambda c: str(c).lower()).copy()
    missing = [c for c in ("time", "icao24", "lat", "lon") if c not in out.columns]
    if missing or not ({"geoaltitude", "baroaltitude"} & set(out.columns)):
        raise ValueError(f"not an OpenSky state-vector table: missing {missing or ['geoaltitude/baroaltitude']}")
    for c in ("time", "lastposupdate", "lastcontact"):
        if c in out.columns:
            out[c] = _to_epoch_s(out[c], c)
    for c in _OPENSKY_NUMERIC:
        if c in out.columns and c not in ("lastposupdate", "lastcontact"):
            out[c] = pd.to_numeric(out[c], errors="coerce").astype(float)
    for c in _OPENSKY_FLAGS:
        if c in out.columns:
            out[c] = _flag(out[c])
    if "squawk" in out.columns:
        out["squawk"] = _squawk(out["squawk"])
    return out


def _check_units(df: pd.DataFrame, aliases: dict | None) -> None:
    """Refuse traffic-renamed frames (altitude, groundspeed, vertical_rate) whose units are not known."""
    explicit = {str(k).lower() for k in (aliases or {})}
    bad = [c for c in df.columns if str(c).lower() in UNIT_AMBIGUOUS_COLUMNS and str(c).lower() not in explicit]
    if bad:
        raise ValueError(
            f"columns {bad} have no stated units (the traffic library renames OpenSky columns and converts "
            "to ft, kt, ft/min). Use the raw OpenSky names (geoaltitude, velocity, vertrate; m, m/s), or "
            "convert to the canonical SI columns and map them via tracks.columns.")


def normalize_frame(df: pd.DataFrame, aliases: dict | None = None) -> pd.DataFrame:
    """Rename columns to the canonical schema and convert time to epoch seconds (``t_s``).

    Idempotent. Adds a bool ``onground`` column (False where the source has none).
    """
    if is_opensky_states(df):
        df = parse_opensky_states(df)
    _check_units(df, aliases)
    # alias keys match column names case-insensitively (as _check_units does); user aliases win
    amap = {str(k).lower(): v for k, v in {**DEFAULT_ALIASES, **(aliases or {})}.items()}
    df = df.rename(columns={c: amap.get(str(c).lower(), c) for c in df.columns}).copy()
    for col in TRACK_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan if col not in ("icao24", "callsign") else ""
    t = df["time_utc"]
    if pd.api.types.is_numeric_dtype(t):
        df["t_s"] = t.astype(float)
    else:
        ts = pd.to_datetime(t, utc=True, format="ISO8601")
        df["t_s"] = (ts - pd.Timestamp(0, tz="UTC")).dt.total_seconds()
    # OpenSky state vectors: `time` is the snapshot time; the position itself is from `lastposupdate`
    # (it can be seconds older). Use the position time, and drop repeated stale positions.
    if "lastposupdate" in df.columns:
        lpu = pd.to_numeric(df["lastposupdate"], errors="coerce")
        df["t_s"] = np.where(np.isfinite(lpu), lpu, df["t_s"])
        df = df.drop_duplicates(subset=["icao24", "t_s"], keep="first") if "icao24" in df.columns else df
    df["icao24"] = df["icao24"].astype(str).str.strip().str.lower()
    df["callsign"] = df["callsign"].fillna("").astype(str).str.strip()
    df["onground"] = _flag(df["onground"]) if "onground" in df.columns else False
    return df


def tracks_from_frame(df: pd.DataFrame, params: TrackParams = TrackParams(), aliases: dict | None = None) -> list[Track]:
    """Group fixes by aircraft (raw, uncleaned). Picks the altitude source per aircraft."""
    df = normalize_frame(df, aliases)
    df = df.dropna(subset=["t_s", "lat", "lon"]).sort_values(["icao24", "t_s"])
    out = []
    for icao, g in df.groupby("icao24", sort=False):
        g = g.drop_duplicates("t_s", keep="last")
        geo_ok = g["geometric_alt_m"].notna().mean()
        src = "geometric" if geo_ok >= params.min_geometric_frac else "baro"
        alt = g["geometric_alt_m" if src == "geometric" else "baro_alt_m"]
        g = g[alt.notna()]
        alt = alt[alt.notna()]
        if len(g) == 0:
            continue
        vel = None
        if g["ground_speed_mps"].notna().all() and g["track_deg"].notna().all():
            vel = velocity_enu_from_adsb(g["ground_speed_mps"].to_numpy(float), g["track_deg"].to_numpy(float),
                                         g["vertical_rate_mps"].to_numpy(float))
        cs = g["callsign"][g["callsign"] != ""]
        out.append(Track(
            icao24=str(icao), callsign=str(cs.iloc[0]) if len(cs) else "", t_s=g["t_s"].to_numpy(float),
            lat_deg=g["lat"].to_numpy(float), lon_deg=g["lon"].to_numpy(float), alt_m=alt.to_numpy(float),
            vel_enu=vel, alt_source=src,
        ))
    return out


# ---------------------------------------------------------------------------
# Cleaning and interpolation
# ---------------------------------------------------------------------------
def _subset(tr: Track, m: NDArray) -> Track:
    return Track(tr.icao24, tr.callsign, tr.t_s[m], tr.lat_deg[m], tr.lon_deg[m], tr.alt_m[m],
                 None if tr.vel_enu is None else tr.vel_enu[m], tr.alt_source, tr.type_code, tr.cls)


def outlier_mask(tr: Track, p: TrackParams) -> NDArray:
    """True for fixes to keep: position spikes (vs neighbours) and altitude spikes removed."""
    n = len(tr.t_s)
    keep = np.ones(n, bool)
    if n < 3:
        return keep
    k = min(5, n - (1 - n % 2))
    alt_med = median_filter(tr.alt_m, size=max(k, 3), mode="nearest")
    keep &= np.abs(tr.alt_m - alt_med) <= p.alt_outlier_m
    xyz = geodetic_to_ecef(tr.lat_deg, tr.lon_deg, alt_med)  # horizontal test, altitude spikes excluded
    t = tr.t_s
    for _ in range(20):  # a spike also inflates its neighbours' residuals: drop local maxima, repeat
        idx = np.flatnonzero(keep)
        if len(idx) < 3:
            break
        ti, xi = t[idx], xyz[idx]
        w = ((ti[1:-1] - ti[:-2]) / (ti[2:] - ti[:-2]))[:, None]
        pred = xi[:-2] + w * (xi[2:] - xi[:-2])
        dev = np.r_[0.0, np.linalg.norm(xi[1:-1] - pred, axis=1), 0.0]
        step = np.linalg.norm(np.diff(xi, axis=0), axis=1) / np.maximum(np.diff(ti), 1e-6)
        fast_l, fast_r = np.r_[False, step > p.max_speed_mps], np.r_[step > p.max_speed_mps, False]
        peak = (dev >= np.r_[0.0, dev[:-1]]) & (dev >= np.r_[dev[1:], 0.0])
        bad = ((dev > p.outlier_m) & peak) | (fast_l & fast_r)
        if len(step) > 1:  # ends can only be judged by implied speed
            bad[0] |= step[0] > p.max_speed_mps and step[1] <= p.max_speed_mps
            bad[-1] |= step[-1] > p.max_speed_mps and step[-2] <= p.max_speed_mps
        if not bad.any():
            break
        keep[idx[bad]] = False
    return keep


def clean_track(tr: Track, p: TrackParams = TrackParams()) -> list[Track]:
    """Remove outliers and split at gaps longer than ``max_gap_s``. Returns gap-free segments."""
    tr = _subset(tr, outlier_mask(tr, p))
    if len(tr.t_s) == 0:
        return []
    breaks = np.flatnonzero(np.diff(tr.t_s) > p.max_gap_s) + 1
    segs = []
    for a, b in zip(np.r_[0, breaks], np.r_[breaks, len(tr.t_s)]):
        if b - a >= p.min_points:
            segs.append(_subset(tr, np.arange(a, b)))
    return segs


def interpolate_track(seg: Track, dt_s: float) -> Track:
    """Resample a gap-free segment onto a uniform grid aligned to multiples of dt (PCHIP in ECEF).

    Velocity: reported ADS-B velocity interpolated component-wise (no heading
    wrap issues), or the PCHIP derivative when absent / velocity_source=derivative.
    """
    t = seg.t_s
    tg = np.arange(np.ceil(t[0] / dt_s) * dt_s, t[-1] + 1e-9, dt_s)
    pch = PchipInterpolator(t, seg.ecef(), axis=0)
    xyz = pch(tg)
    lat, lon, h = ecef_to_geodetic(xyz)
    if seg.vel_enu is not None:
        vel = np.stack([np.interp(tg, t, seg.vel_enu[:, k]) for k in range(3)], axis=-1)
    else:
        v_ecef = pch.derivative()(tg)
        vel = np.einsum("nij,nj->ni", enu_basis(lat, lon), v_ecef)
    return Track(seg.icao24, seg.callsign, tg, lat, lon, h, vel, seg.alt_source, seg.type_code, seg.cls)


def prepare_tracks(tracks: list[Track], p: TrackParams = TrackParams()) -> list[Track]:
    """Clean + split + resample every track. Segments keep their aircraft's icao24."""
    out = []
    for tr in tracks:
        for seg in clean_track(tr, p):
            if p.velocity_source == "derivative":
                seg = Track(seg.icao24, seg.callsign, seg.t_s, seg.lat_deg, seg.lon_deg, seg.alt_m, None,
                            seg.alt_source, seg.type_code, seg.cls)
            out.append(interpolate_track(seg, p.resample_s))
    return out


# ---------------------------------------------------------------------------
# Vectorised track container
# ---------------------------------------------------------------------------
@dataclass
class TrackSet:
    """K track segments on one uniform time grid (ECEF), NaN where a segment has no data.

    Built for a time window only (event window ± propagation delay, or a chunk
    of a long clip) so memory scales with the span needed, not the clip.
    """

    icao24: NDArray
    callsign: NDArray
    cls: NDArray            # (K,) int8 class codes
    alt_source: NDArray     # (K,) "geometric" | "baro"
    t0_s: float
    dt_s: float
    xyz: NDArray            # (K, T, 3)
    vel: NDArray            # (K, T, 3) ECEF
    h_m: NDArray            # (K, T) ellipsoidal height
    ground_speed_mps: NDArray  # (K, T)
    meta: dict = field(default_factory=dict)

    @property
    def n_tracks(self) -> int:
        return self.xyz.shape[0]

    @property
    def t_s(self) -> NDArray:
        return self.t0_s + self.dt_s * np.arange(self.xyz.shape[1])

    def valid(self) -> NDArray:
        return np.isfinite(self.xyz[..., 0])

    def coverage(self, k: int) -> tuple[float, float]:
        """(first, last) valid sample time of track row ``k`` (NaN, NaN if empty). Rows are gap-free segments."""
        cache = self.meta.setdefault("_coverage", {})
        k = int(k)
        if k not in cache:
            idx = np.flatnonzero(np.isfinite(self.xyz[k, :, 0]))
            cache[k] = ((self.t0_s + self.dt_s * idx[0], self.t0_s + self.dt_s * idx[-1]) if len(idx)
                        else (np.nan, np.nan))
        return cache[k]

    def active_between(self, t_lo: float, t_hi: float) -> NDArray:
        """(K,) tracks with any valid sample in [t_lo, t_hi]."""
        m = (self.t_s >= t_lo) & (self.t_s <= t_hi)
        return self.valid()[:, m].any(axis=1)

    def sample(self, k: NDArray, t: NDArray) -> tuple[NDArray, NDArray]:
        """Linear interpolation of position and velocity for track index ``k`` at times ``t``.

        ``k`` broadcasts against ``t`` (e.g. k (P, 1), t (P, M)). Outside a track's
        coverage the result is NaN, so callers can mask.

        A scalar ``k`` indexes that track's rows directly (the scoring hot path: same arithmetic, no
        broadcast of ``k``; profiled in session 5).
        """
        if np.ndim(k) == 0:
            return self._sample_one(int(k), np.asarray(t, float))
        k, t = np.broadcast_arrays(np.asarray(k), np.asarray(t, float))
        u = (t - self.t0_s) / self.dt_s
        i0 = np.floor(u).astype(np.int64)
        T = self.xyz.shape[1]
        ok = (i0 >= 0) & (i0 + 1 < T)
        i0c = np.clip(i0, 0, T - 2)
        w = (u - i0c)[..., None]
        p = (1 - w) * self.xyz[k, i0c] + w * self.xyz[k, i0c + 1]
        v = (1 - w) * self.vel[k, i0c] + w * self.vel[k, i0c + 1]
        p[~ok] = np.nan
        v[~ok] = np.nan
        return p, v

    def _packed(self, k: int) -> NDArray:
        """Row ``k`` as (T-1, 12) [x_i, x_i+1, v_i, v_i+1], so interpolation needs one gather (built on first use).

        Derived from ``xyz`` / ``vel``, which must not change afterwards.
        """
        cache = self.meta.setdefault("_packed", {})
        if k not in cache:
            X, V = self.xyz[k], self.vel[k]
            cache[k] = np.ascontiguousarray(np.concatenate([X[:-1], X[1:], V[:-1], V[1:]], axis=1))
        return cache[k]

    def _sample_one(self, k: int, t: NDArray) -> tuple[NDArray, NDArray]:
        """``sample`` for one track: identical interpolation and arithmetic, one gather from ``_packed``."""
        u = (t - self.t0_s) / self.dt_s
        i0 = np.floor(u).astype(np.int64)
        T = self.xyz.shape[1]
        ok = (i0 >= 0) & (i0 + 1 < T)
        i0c = np.minimum(np.maximum(i0, 0), T - 2)
        w = (u - i0c)[..., None]
        g = self._packed(k)[i0c]                     # (..., 12)
        w1 = 1 - w
        p = w1 * g[..., 0:3] + w * g[..., 3:6]
        v = w1 * g[..., 6:9] + w * g[..., 9:12]
        if not ok.all():
            p[~ok] = np.nan
            v[~ok] = np.nan
        return p, v


def build_trackset(tracks: list[Track], t_start_s: float, t_end_s: float, dt_s: float = 0.25) -> TrackSet:
    """Stack resampled tracks overlapping [t_start, t_end] onto a common grid."""
    t0 = np.floor(t_start_s / dt_s) * dt_s
    T = int(np.ceil((t_end_s - t0) / dt_s)) + 1
    sel = [tr for tr in tracks if tr.t_s[-1] >= t0 and tr.t_s[0] <= t0 + (T - 1) * dt_s]
    K = len(sel)
    xyz = np.full((K, T, 3), np.nan)
    vel = np.full((K, T, 3), np.nan)
    h = np.full((K, T), np.nan)
    gs = np.full((K, T), np.nan)
    for k, tr in enumerate(sel):
        idx = np.rint((tr.t_s - t0) / dt_s).astype(np.int64)
        if not np.allclose(tr.t_s, t0 + idx * dt_s, rtol=0, atol=1e-6):
            raise ValueError(f"track {tr.icao24} is not on the {dt_s}s grid; run prepare_tracks first")
        m = (idx >= 0) & (idx < T)
        xyz[k, idx[m]] = tr.ecef()[m]
        vel[k, idx[m]] = tr.vel_ecef()[m]
        h[k, idx[m]] = tr.alt_m[m]
        ve = tr.vel_enu if tr.vel_enu is not None else None
        gs[k, idx[m]] = np.hypot(ve[m, 0], ve[m, 1]) if ve is not None else np.linalg.norm(vel[k, idx[m]], axis=-1)
    return TrackSet(
        icao24=np.array([tr.icao24 for tr in sel], dtype=object),
        callsign=np.array([tr.callsign for tr in sel], dtype=object),
        cls=np.array([tr.cls for tr in sel], dtype=np.int8),
        alt_source=np.array([tr.alt_source for tr in sel], dtype=object),
        t0_s=float(t0), dt_s=dt_s, xyz=xyz, vel=vel, h_m=h, ground_speed_mps=gs,
    )
