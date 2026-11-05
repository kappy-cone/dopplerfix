"""adsb.lol historical traces -> the canonical track schema (docs/adsblol_ingest.md).

adsb.lol publishes one whole-world archive per day: a tar (split into parts) of readsb trace files, one gzipped
JSON per aircraft. A trace file is ``{"icao", "timestamp", "trace": [...], "r", "t", ...}``. Each trace point is
an array, in the order the readsb README documents (README-json.md, "trace jsons"):

    0 seconds after timestamp | 1 lat | 2 lon | 3 altitude ft, "ground" or null | 4 ground speed kt |
    5 track deg (true heading when on the ground) | 6 flags | 7 vertical rate ft/min | 8 aircraft object or null |
    9 position source (files from 2022 on) | 10 geometric altitude ft | 11 geometric vertical rate ft/min |
    12 indicated airspeed | 13 roll

Flags: 1 stale position, 2 start of a new leg, 4 vertical rate is geometric, 8 index-3 altitude is geometric.
readsb's aircraft.json notes say geometric altitude is referenced to the WGS84 ellipsoid, which is what the
pipeline expects. Check it on real data before relying on it, as for OpenSky.

Pure functions only: the archive is read by ``export.iter_archive_json`` and written by ``cli.py adsblol``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator

import numpy as np
import pandas as pd

from .tracks import TRACK_COLUMNS

FT_M = 0.3048
KT_MPS = 1852.0 / 3600.0
FPM_MPS = FT_M / 60.0
# canonical columns plus what an adsb.lol trace adds (kept for diagnosis; tracks.py ignores extra columns)
EXTRA_COLUMNS = ["onground", "source", "stale", "new_leg"]
COLUMNS = TRACK_COLUMNS + EXTRA_COLUMNS


@dataclass(frozen=True)
class AdsbLolParams:
    """config.yaml ``adsblol:``. Which position sources to keep, and the crop margins."""

    sources: tuple[str, ...] = ("adsb_icao", "adsb_icao_nt", "adsr_icao", "tisb_icao", "mlat")
    keep_unknown_source: bool = True     # points with no source field (files before 2022)
    keep_anonymous: bool = True          # non-ICAO addresses ("~..."): no type, so class unknown
    keep_stale: bool = False             # points readsb marks stale (no position for 20 s before)
    margin_m: float = 30000.0            # crop margin around the region (≥ the audibility slant distances)
    pad_s: float = 300.0                 # crop margin around the time window

    @classmethod
    def from_dict(cls, d: dict | None) -> "AdsbLolParams":
        kw = {k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__}
        if "sources" in kw:
            kw["sources"] = tuple(str(s) for s in kw["sources"])
        return cls(**kw)


def _num(x) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else np.nan


def trace_time_span(obj: dict) -> tuple[float, float]:
    """(first, last) epoch time of a trace file without parsing every point (NaN, NaN if empty)."""
    tr = obj.get("trace") or []
    if not tr:
        return (np.nan, np.nan)
    t0 = float(obj["timestamp"])
    return (t0 + float(tr[0][0]), t0 + float(tr[-1][0]))


def parse_trace(obj: dict) -> pd.DataFrame:
    """One trace file (decoded JSON) -> rows in ``COLUMNS``. Units: m, m/s, deg, epoch seconds.

    - ``baro_alt_m`` from index 3 unless flag 8 says it is geometric; ``geometric_alt_m`` from index 10, else
      from index 3 when flag 8 is set. "ground" means on the ground: both altitudes NaN, ``onground`` True.
    - ``vertical_rate_mps``: the geometric rate (index 11) when given, else index 7.
    - ``callsign``: the aircraft object's ``flight``, carried forward within the trace (it is only sent now and
      then), and stripped.
    """
    icao = str(obj.get("icao", "")).strip().lower()
    t0 = float(obj.get("timestamp", np.nan))
    rows, cs = [], ""
    for p in obj.get("trace") or []:
        p = list(p) + [None] * (14 - len(p))
        alt, flags = p[3], int(p[6] or 0)
        ground = alt == "ground"
        a3 = np.nan if ground else _num(alt)
        geom = _num(p[10])
        if np.isnan(geom) and flags & 8:
            geom = a3
        baro = np.nan if flags & 8 else a3
        vr = _num(p[11]) if p[11] is not None else _num(p[7])
        ac = p[8] if isinstance(p[8], dict) else {}
        if ac.get("flight"):
            cs = str(ac["flight"]).strip()
        rows.append((t0 + float(p[0]), icao, cs, _num(p[1]), _num(p[2]), geom * FT_M, baro * FT_M,
                     _num(p[4]) * KT_MPS, _num(p[5]), vr * FPM_MPS, ground,
                     "" if p[9] is None else str(p[9]), bool(flags & 1), bool(flags & 2)))
    return pd.DataFrame(rows, columns=COLUMNS)


def select_points(df: pd.DataFrame, p: AdsbLolParams) -> pd.DataFrame:
    """Keep the accepted position sources (and, unless asked, drop stale and anonymous points)."""
    ok = df["source"].isin(p.sources) | ((df["source"] == "") & p.keep_unknown_source)
    if not p.keep_stale:
        ok &= ~df["stale"]
    if not p.keep_anonymous:
        ok &= ~df["icao24"].str.startswith("~")
    return df[ok]


def crop(df: pd.DataFrame, bbox: tuple[float, float, float, float], t_lo_s: float, t_hi_s: float) -> pd.DataFrame:
    """Rows inside ``bbox`` = (lat_lo, lat_hi, lon_lo, lon_hi) and [t_lo, t_hi]."""
    la0, la1, lo0, lo1 = bbox
    m = (df["lat"].between(la0, la1) & df["lon"].between(lo0, lo1) & df["time_utc"].between(t_lo_s, t_hi_s))
    return df[m]


def padded_bbox(lat_lo: float, lat_hi: float, lon_lo: float, lon_hi: float, margin_m: float) -> tuple[float, ...]:
    """The bbox grown by ``margin_m`` on every side (spherical approximation, conservative at the poleward edge)."""
    dlat = np.degrees(margin_m / 6371000.0)
    lat_ref = min(89.0, max(abs(lat_lo), abs(lat_hi)) + dlat)
    dlon = dlat / np.cos(np.radians(lat_ref))
    return (lat_lo - dlat, lat_hi + dlat, lon_lo - dlon, lon_hi + dlon)


def type_row(obj: dict) -> dict | None:
    """Registry row (aircraft_types.build_lookup columns) from a trace file's database fields, if any."""
    t = str(obj.get("t") or "").strip().upper()
    if not t:
        return None
    return {"icao24": str(obj.get("icao", "")).strip().lower(), "typecode": t,
            "registration": str(obj.get("r") or "").strip()}


def extract(traces: Iterable[dict], bbox: tuple[float, float, float, float], t_lo_s: float, t_hi_s: float,
            p: AdsbLolParams) -> Iterator[tuple[pd.DataFrame, dict | None]]:
    """Per trace file overlapping the window and box: (cropped rows, registry row). Others are skipped cheaply.

    ``bbox`` and the window should already include the margins (``padded_bbox``, ``p.pad_s``).
    """
    for obj in traces:
        a, b = trace_time_span(obj)
        if not (b >= t_lo_s and a <= t_hi_s):
            continue
        df = crop(select_points(parse_trace(obj), p), bbox, t_lo_s, t_hi_s)
        if len(df):
            yield df, type_row(obj)
