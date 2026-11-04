"""Coordinate frames and terrain.

All distance/Doppler geometry runs in ECEF (EPSG:4978, metres), which gives
exact 3-D slant ranges anywhere with no tile seams. Local ENU is used only for
building synthetic scenarios and for plotting. Heights are ellipsoidal (WGS84),
matching ADS-B geometric altitude; DEMs are converted by adding the geoid
undulation (see :class:`ArrayDEM`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator, Protocol

import numpy as np
import pymap3d
import shapely
from numpy.typing import ArrayLike, NDArray
from pyproj import CRS, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info

log = logging.getLogger(__name__)


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


class DEMSource(Protocol):
    """Windowed DEM access: one in-memory DEM per grid tile (the raster reader lives in cli.py)."""

    def for_bounds(self, lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> DEM:
        ...


@dataclass(frozen=True)
class FlatDEM:
    """Constant ellipsoidal ground height (tests and synthetic scenarios). Also its own DEMSource."""

    elev_m: float = 0.0

    def sample(self, lat: ArrayLike, lon: ArrayLike) -> NDArray:
        return np.full(np.broadcast(np.asarray(lat), np.asarray(lon)).shape, self.elev_m, float)

    def for_bounds(self, lon_min: float, lat_min: float, lon_max: float, lat_max: float) -> "FlatDEM":
        return self


# ---------------------------------------------------------------------------
# Geoid
# ---------------------------------------------------------------------------
class GeoidModel:
    """Geoid undulation N (m) such that ellipsoidal h = orthometric H + N.

    Samples PROJ geoid grids through a ``vgridshift`` pipeline; with several
    grids the first one covering a point is used (e.g. GEOID18 inside CONUS,
    EGM96 elsewhere). The caller must have put the grid directory on pyproj's
    data path (cli.py does). If no grid can be opened, or a point is outside
    every grid, the constant ``fallback_m`` is used and a warning is logged.
    """

    def __init__(self, grids: list[str] | tuple[str, ...] = (), fallback_m: float = 0.0):
        self.grids = tuple(grids)
        self.fallback_m = float(fallback_m)
        self._tr = None
        self.source = "constant"
        found = []
        for g in self.grids:  # keep only grids PROJ can actually open
            try:
                Transformer.from_pipeline(self._pipe(g)).transform(np.array([0.0]), np.array([0.0]), np.array([0.0]))
                found.append(g)
            except Exception as exc:  # pyproj raises ProjError subclasses
                log.warning("geoid grid %s unavailable (%s)", g, exc)
        if found:
            self._tr = Transformer.from_pipeline(self._pipe(",".join(found)))
            self.source = "grid:" + ",".join(found)
        if self._tr is None:
            log.warning("no geoid grid found: using constant undulation %.2f m (fallback)", self.fallback_m)
        self._warned_outside = False

    @staticmethod
    def _pipe(grids: str) -> str:
        return ("+proj=pipeline +step +proj=axisswap +order=2,1 +step +proj=unitconvert +xy_in=deg +xy_out=rad "
                f"+step +proj=vgridshift +grids={grids} +multiplier=1 "
                "+step +proj=unitconvert +xy_in=rad +xy_out=deg +step +proj=axisswap +order=2,1")

    def __call__(self, lat: ArrayLike, lon: ArrayLike) -> NDArray:
        lat, lon = np.broadcast_arrays(np.asarray(lat, float), np.asarray(lon, float))
        if self._tr is None:
            return np.full(lat.shape, self.fallback_m)
        _, _, n = self._tr.transform(lat.ravel(), lon.ravel(), np.zeros(lat.size))
        n = np.asarray(n, float).reshape(lat.shape)
        bad = ~np.isfinite(n)
        if bad.any():
            if not self._warned_outside:
                log.warning("%d points outside every geoid grid: constant %.2f m used there", bad.sum(), self.fallback_m)
                self._warned_outside = True
            n = np.where(bad, self.fallback_m, n)
        return n


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


# ---------------------------------------------------------------------------
# Candidate grid
# ---------------------------------------------------------------------------
def grid_crs(region_lonlat: "shapely.Geometry", mode: str = "regional", crs: str = "auto") -> CRS:
    """Projected CRS for indexing the grid: UTM zone of the region (regional) or EPSG:5070 (us)."""
    if crs and crs != "auto":
        return CRS.from_user_input(crs)
    if mode == "us":
        return CRS.from_epsg(5070)
    c = region_lonlat.centroid
    info = query_utm_crs_info(datum_name="WGS 84",
                              area_of_interest=AreaOfInterest(c.x, c.y, c.x, c.y))
    return CRS.from_epsg(int(info[0].code))


@dataclass
class GridTile:
    """Cells of one grid tile (in memory only). Cell (i, j) has centre ((i+0.5)·s, (j+0.5)·s) in the grid CRS."""

    i: NDArray            # (M,) int64 column index (x)
    j: NDArray            # (M,) int64 row index (y)
    x_m: NDArray          # (M,) grid-CRS easting of the cell centre
    y_m: NDArray
    lat: NDArray
    lon: NDArray
    ground_h_m: NDArray   # ellipsoidal ground height
    xyz: NDArray          # (M, 3) float64 ECEF of the microphone (ground + mic height)

    def __len__(self) -> int:
        return len(self.i)


@dataclass(frozen=True)
class GridSpec:
    crs: CRS
    spacing_m: float
    region: "shapely.Geometry"     # in the grid CRS
    tile_cells: int

    def tile_origins(self) -> NDArray:
        """(n, 2) integer (i0, j0) of tiles whose square intersects the region."""
        s, n = self.spacing_m, self.tile_cells
        x0, y0, x1, y1 = self.region.bounds
        ti = np.arange(np.floor(x0 / (s * n)), np.ceil(x1 / (s * n)), dtype=np.int64)
        tj = np.arange(np.floor(y0 / (s * n)), np.ceil(y1 / (s * n)), dtype=np.int64)
        TI, TJ = np.meshgrid(ti, tj, indexing="ij")
        TI, TJ = TI.ravel(), TJ.ravel()
        boxes = shapely.box(TI * n * s, TJ * n * s, (TI + 1) * n * s, (TJ + 1) * n * s)
        keep = shapely.intersects(boxes, self.region)
        return np.c_[TI[keep] * n, TJ[keep] * n]

    def tile_centres(self, origins: NDArray) -> NDArray:
        h = 0.5 * self.tile_cells * self.spacing_m
        return origins * self.spacing_m + h

    @property
    def tile_half_diag_m(self) -> float:
        return float(np.sqrt(2) * 0.5 * self.tile_cells * self.spacing_m)

    @property
    def cell_half_diag_m(self) -> float:
        return float(np.sqrt(2) * 0.5 * self.spacing_m)

    def cell_ids(self, i: NDArray, j: NDArray) -> NDArray:
        """Stable int64 id per cell (unique within any realistic grid)."""
        return (np.asarray(i, np.int64) << 32) + (np.asarray(j, np.int64) & 0xFFFFFFFF)


def make_grid_spec(region_lonlat: "shapely.Geometry", spacing_m: float, tile_cells: int,
                   mode: str = "regional", crs: str = "auto") -> GridSpec:
    gcrs = grid_crs(region_lonlat, mode, crs)
    tr = Transformer.from_crs("EPSG:4326", gcrs, always_xy=True)
    region = shapely.transform(region_lonlat, lambda xy: np.c_[tr.transform(xy[:, 0], xy[:, 1])])
    return GridSpec(gcrs, float(spacing_m), region, int(tile_cells))


def tiles_to_geodetic(spec: GridSpec) -> Transformer:
    return Transformer.from_crs(spec.crs, "EPSG:4326", always_xy=True)


def tile_ecef_centres(spec: GridSpec, origins: NDArray, h_m: float = 0.0) -> NDArray:
    """ECEF of tile centres at a nominal height (for coarse spatial pruning)."""
    c = spec.tile_centres(origins)
    lon, lat = tiles_to_geodetic(spec).transform(c[:, 0], c[:, 1])
    return geodetic_to_ecef(lat, lon, np.full(len(c), h_m))


def build_tile(spec: GridSpec, i0: int, j0: int, dem_source: DEMSource, mic_height_m: float,
               to_geo: Transformer | None = None) -> GridTile:
    """Cells of the tile at (i0, j0) inside the region, with windowed DEM heights and ECEF mic positions."""
    s, n = spec.spacing_m, spec.tile_cells
    I, J = np.meshgrid(np.arange(i0, i0 + n, dtype=np.int64), np.arange(j0, j0 + n, dtype=np.int64), indexing="ij")
    I, J = I.ravel(), J.ravel()
    x, y = (I + 0.5) * s, (J + 0.5) * s
    inside = shapely.contains_xy(spec.region, x, y)
    I, J, x, y = I[inside], J[inside], x[inside], y[inside]
    to_geo = to_geo or tiles_to_geodetic(spec)
    lon, lat = to_geo.transform(x, y)
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    if len(I):
        pad = 2 * s / 111_000.0
        dem = dem_source.for_bounds(lon.min() - pad, lat.min() - pad, lon.max() + pad, lat.max() + pad)
        h = np.asarray(dem.sample(lat, lon), float)
    else:
        h = np.zeros(0)
    ok = np.isfinite(h)
    I, J, x, y, lat, lon, h = I[ok], J[ok], x[ok], y[ok], lat[ok], lon[ok], h[ok]
    xyz = geodetic_to_ecef(lat, lon, h + mic_height_m).reshape(-1, 3)
    return GridTile(I, J, x, y, lat, lon, h, xyz)


def cell_polygons_lonlat(spec: GridSpec, i: NDArray, j: NDArray, to_geo: Transformer | None = None):
    """Cell squares as EPSG:4326 polygons (for display layers)."""
    s = spec.spacing_m
    to_geo = to_geo or tiles_to_geodetic(spec)
    i, j = np.asarray(i, np.int64), np.asarray(j, np.int64)
    cx = np.stack([i, i + 1, i + 1, i, i], axis=-1) * s
    cy = np.stack([j, j, j + 1, j + 1, j], axis=-1) * s
    lon, lat = to_geo.transform(cx.ravel(), cy.ravel())
    ring = np.stack([np.asarray(lon), np.asarray(lat)], axis=-1).reshape(len(i), 5, 2)
    return shapely.polygons(ring)


def count_grid_cells(spec: GridSpec, max_tiles: int = 5000) -> tuple[int, bool]:
    """(number of grid cells inside the region, exact?). Exact by tile when tiles ≤ max_tiles, else area-based."""
    origins = spec.tile_origins()
    if len(origins) > max_tiles:
        return int(round(spec.region.area / spec.spacing_m ** 2)), False
    n, s = spec.tile_cells, spec.spacing_m
    off = np.arange(n) + 0.5
    total = 0
    for i0, j0 in origins:
        X, Y = np.meshgrid((i0 + off) * s, (j0 + off) * s, indexing="ij")
        total += int(shapely.contains_xy(spec.region, X.ravel(), Y.ravel()).sum())
    return total, True
