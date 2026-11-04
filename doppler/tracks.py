"""ADS-B tracks: container, tabular conversion. Cleaning/interpolation and
OpenSky ingest arrive in milestones 3 and 7.

Tabular schema (CSV/Parquet), one row per fix:
    time_utc (ISO-8601) | icao24 | callsign | lat | lon | geometric_alt_m |
    ground_speed_mps | track_deg
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray

from .geo import LocalFrame

TRACK_COLUMNS = [
    "time_utc", "icao24", "callsign", "lat", "lon",
    "geometric_alt_m", "ground_speed_mps", "track_deg",
]


@dataclass
class Track:
    """One aircraft's track. Times are epoch seconds (UTC)."""

    icao24: str
    callsign: str
    t_s: NDArray
    lat_deg: NDArray
    lon_deg: NDArray
    alt_m: NDArray

    def enu(self, frame: LocalFrame) -> NDArray:
        """Positions in the local frame, (N, 3) metres."""
        return frame.to_enu(self.lat_deg, self.lon_deg, self.alt_m)

    @classmethod
    def from_enu(cls, icao24: str, callsign: str, t_s: NDArray, enu: NDArray, frame: LocalFrame) -> "Track":
        lat, lon, h = frame.to_geodetic(enu)
        return cls(icao24, callsign, np.asarray(t_s, float), lat, lon, h)


def ground_kinematics(t_s: NDArray, enu: NDArray) -> tuple[NDArray, NDArray]:
    """Ground speed (m/s) and track angle (deg clockwise from north) by finite differences."""
    vel = np.gradient(enu, t_s, axis=0)
    gs = np.hypot(vel[:, 0], vel[:, 1])
    trk = np.degrees(np.arctan2(vel[:, 0], vel[:, 1])) % 360.0
    return gs, trk


def tracks_to_frame(tracks: list[Track], frame: LocalFrame) -> pd.DataFrame:
    """Flatten tracks to the tabular schema (time converted to UTC timestamps)."""
    rows = []
    for tr in tracks:
        gs, trk = ground_kinematics(tr.t_s, tr.enu(frame))
        rows.append(pd.DataFrame({
            "time_utc": pd.to_datetime(tr.t_s, unit="s", utc=True),
            "icao24": tr.icao24,
            "callsign": tr.callsign,
            "lat": tr.lat_deg,
            "lon": tr.lon_deg,
            "geometric_alt_m": tr.alt_m,
            "ground_speed_mps": gs,
            "track_deg": trk,
        }))
    return pd.concat(rows, ignore_index=True)[TRACK_COLUMNS]


def tracks_from_frame(df: pd.DataFrame) -> list[Track]:
    """Inverse of :func:`tracks_to_frame` (no cleaning yet)."""
    out = []
    t_all = pd.to_datetime(df["time_utc"], utc=True)
    df = df.assign(_t=(t_all - pd.Timestamp(0, tz="UTC")).dt.total_seconds())
    for icao, g in df.sort_values("_t").groupby("icao24", sort=False):
        out.append(Track(
            icao24=str(icao), callsign=str(g["callsign"].iloc[0]),
            t_s=g["_t"].to_numpy(float), lat_deg=g["lat"].to_numpy(float),
            lon_deg=g["lon"].to_numpy(float), alt_m=g["geometric_alt_m"].to_numpy(float),
        ))
    return out
