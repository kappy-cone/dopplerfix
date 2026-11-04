"""Coordinate frames and terrain.

Geometry runs in a local East-North-Up frame about the search-region centre
(metres). Storage/export is EPSG:4326. Altitudes are geometric heights; keep
the DEM and ADS-B on the same vertical datum (see geoid note in milestone 3).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymap3d
from numpy.typing import ArrayLike, NDArray


@dataclass(frozen=True)
class LocalFrame:
    """ENU frame anchored at (lat0, lon0, h0)."""

    lat0: float
    lon0: float
    h0: float = 0.0

    def to_enu(self, lat: ArrayLike, lon: ArrayLike, h: ArrayLike) -> NDArray:
        """Geodetic -> (..., 3) ENU metres."""
        e, n, u = pymap3d.geodetic2enu(
            np.asarray(lat, float), np.asarray(lon, float), np.asarray(h, float),
            self.lat0, self.lon0, self.h0,
        )
        return np.stack([np.asarray(e), np.asarray(n), np.asarray(u)], axis=-1)

    def to_geodetic(self, enu: ArrayLike) -> tuple[NDArray, NDArray, NDArray]:
        """(..., 3) ENU metres -> (lat, lon, h)."""
        enu = np.asarray(enu, float)
        lat, lon, h = pymap3d.enu2geodetic(
            enu[..., 0], enu[..., 1], enu[..., 2], self.lat0, self.lon0, self.h0
        )
        return np.asarray(lat), np.asarray(lon), np.asarray(h)


@dataclass(frozen=True)
class FlatDEM:
    """Constant-elevation DEM stub (milestone 3 replaces with a rasterio-backed DEM)."""

    elev_m: float = 0.0

    def sample(self, lat: ArrayLike, lon: ArrayLike) -> NDArray:
        return np.full(np.broadcast(np.asarray(lat), np.asarray(lon)).shape, self.elev_m, float)
