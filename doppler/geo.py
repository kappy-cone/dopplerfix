"""Coordinate frames and terrain.

All distance/Doppler geometry runs in ECEF (EPSG:4978, metres), which gives
exact 3-D slant ranges anywhere with no tile seams. Local ENU is used only for
building synthetic scenarios and for plotting. Heights are ellipsoidal (WGS84),
matching ADS-B geometric altitude; DEMs are converted by adding the geoid
undulation (see :class:`ArrayDEM`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pymap3d
from numpy.typing import ArrayLike, NDArray
from pyproj import Transformer


# ---------------------------------------------------------------------------
# ECEF
# ---------------------------------------------------------------------------
def geodetic_to_ecef(lat: ArrayLike, lon: ArrayLike, h: ArrayLike) -> NDArray:
    """WGS84 geodetic (deg, deg, ellipsoidal m) -> (..., 3) ECEF metres."""
    x, y, z = pymap3d.geodetic2ecef(np.asarray(lat, float), np.asarray(lon, float), np.asarray(h, float))
    return np.stack([np.asarray(x), np.asarray(y), np.asarray(z)], axis=-1)


def ecef_to_geodetic(xyz: ArrayLike) -> tuple[NDArray, NDArray, NDArray]:
    """(..., 3) ECEF -> (lat, lon, h)."""
    xyz = np.asarray(xyz, float)
    lat, lon, h = pymap3d.ecef2geodetic(xyz[..., 0], xyz[..., 1], xyz[..., 2])
    return np.asarray(lat), np.asarray(lon), np.asarray(h)


def enu_basis(lat: ArrayLike, lon: ArrayLike) -> NDArray:
    """(..., 3, 3) rows = east, north, up unit vectors in ECEF at each point."""
    la, lo = np.deg2rad(np.asarray(lat, float)), np.deg2rad(np.asarray(lon, float))
    sl, cl, so, co = np.sin(la), np.cos(la), np.sin(lo), np.cos(lo)
    e = np.stack([-so, co, np.zeros_like(so)], axis=-1)
    n = np.stack([-sl * co, -sl * so, cl], axis=-1)
    u = np.stack([cl * co, cl * so, sl], axis=-1)
    return np.stack([e, n, u], axis=-2)


def enu_vector_to_ecef(v_enu: ArrayLike, lat: ArrayLike, lon: ArrayLike) -> NDArray:
    """Rotate local (east, north, up) vectors into ECEF (e.g. ADS-B velocity)."""
    B = enu_basis(lat, lon)
    return np.einsum("...i,...ij->...j", np.asarray(v_enu, float), B)


def velocity_enu_from_adsb(ground_speed_mps: ArrayLike, track_deg: ArrayLike,
                           vertical_rate_mps: ArrayLike | None = None) -> NDArray:
    """(gs, track clockwise from north, vertical rate) -> (..., 3) ENU velocity."""
    gs = np.asarray(ground_speed_mps, float)
    tr = np.deg2rad(np.asarray(track_deg, float))
    vz = np.zeros_like(gs) if vertical_rate_mps is None else np.nan_to_num(np.asarray(vertical_rate_mps, float))
    return np.stack([gs * np.sin(tr), gs * np.cos(tr), vz], axis=-1)


@dataclass(frozen=True)
class LocalFrame:
    """ENU frame anchored at (lat0, lon0, h0). For synthetic scenarios and plotting."""

    lat0: float
    lon0: float
    h0: float = 0.0

    def to_enu(self, lat: ArrayLike, lon: ArrayLike, h: ArrayLike) -> NDArray:
        e, n, u = pymap3d.geodetic2enu(
            np.asarray(lat, float), np.asarray(lon, float), np.asarray(h, float), self.lat0, self.lon0, self.h0
        )
        return np.stack([np.asarray(e), np.asarray(n), np.asarray(u)], axis=-1)

    def to_geodetic(self, enu: ArrayLike) -> tuple[NDArray, NDArray, NDArray]:
        enu = np.asarray(enu, float)
        lat, lon, h = pymap3d.enu2geodetic(enu[..., 0], enu[..., 1], enu[..., 2], self.lat0, self.lon0, self.h0)
        return np.asarray(lat), np.asarray(lon), np.asarray(h)

    def enu_to_ecef(self, enu: ArrayLike) -> NDArray:
        return geodetic_to_ecef(*self.to_geodetic(enu))

    def ecef_to_enu(self, xyz: ArrayLike) -> NDArray:
        return self.to_enu(*ecef_to_geodetic(xyz))


# ---------------------------------------------------------------------------
# Terrain
# ---------------------------------------------------------------------------
class DEM(Protocol):
    def sample(self, lat: ArrayLike, lon: ArrayLike) -> NDArray:
        """Ellipsoidal ground height (m) at the given points."""


@dataclass(frozen=True)
class FlatDEM:
    """Constant ellipsoidal ground height (tests and synthetic scenarios)."""

    elev_m: float = 0.0

    def sample(self, lat: ArrayLike, lon: ArrayLike) -> NDArray:
        return np.full(np.broadcast(np.asarray(lat), np.asarray(lon)).shape, self.elev_m, float)


class ArrayDEM:
    """Bilinear sampling of an in-memory DEM raster (any CRS).

    ``elev`` is (rows, cols) with ``transform`` an affine.Affine pixel->CRS
    mapping (rasterio convention, pixel corners). Heights are converted to
    ellipsoidal by adding ``geoid_undulation_m`` (a constant, or a callable
    ``(lat, lon) -> N``), since DEMs are usually orthometric while ADS-B
    geometric altitude is ellipsoidal. Points outside the raster or on nodata
    return NaN. File reading lives in cli.py; this class is pure.
    """

    def __init__(self, elev: NDArray, transform, crs: str, nodata: float | None = None,
                 geoid_undulation_m: float | callable = 0.0):
        self.elev = np.asarray(elev, float)
        if nodata is not None:
            self.elev = np.where(self.elev == nodata, np.nan, self.elev)
        self.transform = transform
        self.inv = ~transform
        self.crs = crs
        self.geoid = geoid_undulation_m
        self._to_crs = Transformer.from_crs("EPSG:4326", crs, always_xy=True)

    def sample(self, lat: ArrayLike, lon: ArrayLike) -> NDArray:
        lat, lon = np.broadcast_arrays(np.asarray(lat, float), np.asarray(lon, float))
        x, y = self._to_crs.transform(lon, lat)
        A = self.inv
        x, y = np.asarray(x), np.asarray(y)
        col, row = A.a * x + A.b * y + A.c, A.d * x + A.e * y + A.f
        # pixel centres sit at +0.5
        c, r = np.asarray(col) - 0.5, np.asarray(row) - 0.5
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        R, C = self.elev.shape
        ok = (r0 >= 0) & (c0 >= 0) & (r0 + 1 < R) & (c0 + 1 < C)
        r0c, c0c = np.clip(r0, 0, R - 2), np.clip(c0, 0, C - 2)
        E = self.elev
        z = ((1 - fr) * ((1 - fc) * E[r0c, c0c] + fc * E[r0c, c0c + 1])
             + fr * ((1 - fc) * E[r0c + 1, c0c] + fc * E[r0c + 1, c0c + 1]))
        z = np.where(ok, z, np.nan)
        n = self.geoid(lat, lon) if callable(self.geoid) else self.geoid
        return z + n
