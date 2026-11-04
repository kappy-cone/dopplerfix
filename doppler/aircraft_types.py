"""icao24 -> ICAO type designator -> propulsion class (prop / jet / unknown).

Sources (read in cli.py, passed here as DataFrames):
  * an aircraft registry, e.g. the OpenSky aircraft database
    (columns ``icao24``, ``typecode``, optionally ``icaoaircrafttype``), and
  * optionally a type table, e.g. ICAO Doc 8643 (``typecode`` → description such
    as ``L2J``).
The ICAO description is <aircraft kind><engine count><engine type>; the engine
type letter decides the class: J → jet; P (piston), T (turboprop/turboshaft)
and E (electric) → prop. Helicopters (kind H, rotor harmonics) count as prop.
Anything missing or unrecognised is ``unknown``, which matches either event class.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray

PROP, JET, UNKNOWN = 0, 1, 2
CLASS_NAMES = ("prop", "jet", "unknown")
ENGINE_LETTER_CLASS = {"J": JET, "P": PROP, "T": PROP, "E": PROP}


def class_code(name: str) -> int:
    return CLASS_NAMES.index(str(name).lower())


def class_from_description(desc: str | None) -> int:
    """ICAO aircraft description (e.g. 'L2J', 'H1T', 'L1P') -> class code."""
    if not isinstance(desc, str) or len(desc.strip()) < 3:
        return UNKNOWN
    return ENGINE_LETTER_CLASS.get(desc.strip().upper()[2], UNKNOWN)


def compatible(event_cls: ArrayLike, track_cls: ArrayLike) -> NDArray:
    """Class match where 'unknown' on either side matches anything (vectorised)."""
    a, b = np.asarray(event_cls), np.asarray(track_cls)
    return (a == b) | (a == UNKNOWN) | (b == UNKNOWN)


def build_lookup(registry: pd.DataFrame, type_table: pd.DataFrame | None = None) -> pd.DataFrame:
    """Normalised lookup indexed by lower-case icao24 with columns typecode, description, cls.

    The type table's description wins over the registry's own ``icaoaircrafttype``
    when both exist (the registry field is often stale or blank).
    """
    reg = registry.rename(columns=str.lower).copy()
    reg["icao24"] = reg["icao24"].astype(str).str.strip().str.lower()
    reg["typecode"] = reg.get("typecode", pd.Series(index=reg.index, dtype=object)).fillna("").astype(str).str.strip().str.upper()
    desc = reg.get("icaoaircrafttype", pd.Series("", index=reg.index)).fillna("").astype(str)
    if type_table is not None:
        tt = type_table.rename(columns=str.lower)
        dcol = "description" if "description" in tt.columns else "icaoaircrafttype"
        tmap = dict(zip(tt["typecode"].astype(str).str.strip().str.upper(), tt[dcol].astype(str)))
        from_table = reg["typecode"].map(tmap)
        desc = from_table.where(from_table.notna(), desc)
    reg["description"] = desc
    reg["cls"] = [class_from_description(d) for d in desc]
    return reg.drop_duplicates("icao24", keep="last").set_index("icao24")[["typecode", "description", "cls"]]


def classify_icao24(icao24: ArrayLike, lookup: pd.DataFrame) -> tuple[NDArray, NDArray]:
    """(type codes, class codes) for each icao24; unknown when absent from the lookup."""
    keys = pd.Index(np.asarray(icao24, dtype=str)).str.strip().str.lower()
    hit = lookup.reindex(keys)
    types = hit["typecode"].fillna("").to_numpy(str)
    cls = hit["cls"].fillna(UNKNOWN).to_numpy(np.int8)
    return types, cls
