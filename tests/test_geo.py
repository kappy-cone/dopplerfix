import numpy as np
import pytest
from affine import Affine

from doppler import geo
from doppler import physics as ph

FRAME = geo.LocalFrame(40.0394, -105.2258, 1600.0)


def test_ecef_roundtrip_and_known_point():
    xyz = geo.geodetic_to_ecef(0.0, 0.0, 0.0)
    np.testing.assert_allclose(xyz, [6378137.0, 0.0, 0.0], atol=1e-6)
    lat, lon, h = geo.ecef_to_geodetic(geo.geodetic_to_ecef([40.0, -33.0], [-105.0, 151.0], [1600.0, 3000.0]))
    np.testing.assert_allclose(lat, [40.0, -33.0], atol=1e-9)
    np.testing.assert_allclose(lon, [-105.0, 151.0], atol=1e-9)
    np.testing.assert_allclose(h, [1600.0, 3000.0], atol=1e-6)


def test_enu_basis_and_velocity_rotation():
    B = geo.enu_basis(40.0, -105.0)
    np.testing.assert_allclose(B @ B.T, np.eye(3), atol=1e-12)
    # A point 100 m north in ENU, mapped through ECEF, is 100 m from the origin along the north vector.
    p0 = FRAME.enu_to_ecef([0.0, 0.0, 0.0])
    p1 = FRAME.enu_to_ecef([0.0, 100.0, 0.0])
    v = geo.enu_vector_to_ecef([0.0, 100.0, 0.0], FRAME.lat0, FRAME.lon0)
    np.testing.assert_allclose(p1 - p0, v, atol=1e-3)
    np.testing.assert_allclose(geo.velocity_enu_from_adsb(100.0, 90.0, 5.0), [100.0, 0.0, 5.0], atol=1e-9)


def test_doppler_is_frame_independent():
    """The general model gives the same answer in ECEF as in local ENU."""
    t = np.arange(0, 300, 0.25)
    enu = np.c_[-6000 + 75 * t, 400 + 0 * t, 800 + 0 * t]
    mic_enu = np.array([300.0, -200.0, 1.5])
    tr = np.linspace(60, 200, 50)
    r_enu, te_enu, _ = ph.predicted_ratio_at(tr, t, enu, mic_enu)
    r_ecef, te_ecef, _ = ph.predicted_ratio_at(tr, t, FRAME.enu_to_ecef(enu), FRAME.enu_to_ecef(mic_enu))
    np.testing.assert_allclose(r_ecef, r_enu, atol=1e-9)
    np.testing.assert_allclose(te_ecef, te_enu, atol=1e-6)


def test_array_dem_bilinear_geoid_and_bounds():
    # 3x3 DEM in EPSG:4326, 0.01° pixels, heights increasing east (z = 100*col + 10*row)
    elev = np.array([[0, 100, 200], [10, 110, 210], [20, 120, 220]], float)
    tf = Affine(0.01, 0, -105.0, 0, -0.01, 40.0)   # top-left corner at (lon -105, lat 40)
    dem = geo.ArrayDEM(elev, tf, "EPSG:4326", nodata=None, geoid_undulation_m=-17.0)
    # pixel centre (row 1, col 1) -> 110
    assert dem.sample(40.0 - 0.015, -105.0 + 0.015) == pytest.approx(110 - 17.0)
    # halfway between centres of cols 0 and 1 on row 0 -> 50
    assert dem.sample(40.0 - 0.005, -105.0 + 0.01) == pytest.approx(50 - 17.0)
    assert np.isnan(dem.sample(41.0, -105.0))
    holes = geo.ArrayDEM(np.where(elev == 110, -9999, elev), tf, "EPSG:4326", nodata=-9999)
    assert np.isnan(holes.sample(40.0 - 0.015, -105.0 + 0.015))
    assert geo.FlatDEM(1600.0).sample([1, 2], [3, 4]).tolist() == [1600.0, 1600.0]


def test_geoid_grid_and_constant_fallback(caplog):
    import os

    import pyproj

    if not os.path.exists("data/proj/us_noaa_g2018u0.tif"):
        pytest.skip("geoid grids not in data/proj (see CLAUDE.md Known pitfalls)")
    pyproj.datadir.append_data_dir(os.path.abspath("data/proj"))
    g = geo.GeoidModel(["us_noaa_g2018u0.tif", "us_nga_egm96_15.tif"], -99.0)
    assert g.source.startswith("grid:")
    n = g(np.array([40.0394, 0.0]), np.array([-105.2258, 0.0]))
    assert n[0] == pytest.approx(-16.10, abs=0.05)       # GEOID18 near Boulder
    assert n[1] == pytest.approx(17.16, abs=0.05)        # outside CONUS: EGM96
    with caplog.at_level("WARNING"):
        f = geo.GeoidModel(["missing_grid.tif"], -16.0)
    assert f.source == "constant" and f(40.0, -105.0) == -16.0
    assert any("fallback" in r.message for r in caplog.records)


def test_windowed_dem_source_matches_full_raster(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    import cli

    rng = np.random.default_rng(1)
    elev = 1500 + rng.normal(0, 20, (200, 200)).cumsum(0).cumsum(1) / 50
    tr = from_origin(-105.3, 40.1, 0.001, 0.001)
    path = tmp_path / "dem.tif"
    with rasterio.open(path, "w", driver="GTiff", width=200, height=200, count=1, dtype="float64",
                       crs="EPSG:4326", transform=tr) as ds:
        ds.write(elev, 1)
    full = geo.ArrayDEM(elev, tr, "EPSG:4326", None, 0.0)
    src = cli.RasterDEMSource(path, geo.GeoidModel([], 0.0))
    lat = rng.uniform(40.02, 40.08, 50)
    lon = rng.uniform(-105.28, -105.22, 50)
    win = src.for_bounds(lon.min(), lat.min(), lon.max(), lat.max())
    assert win.elev.shape[0] < 200 and win.elev.shape[1] < 200          # windowed, not the whole raster
    np.testing.assert_allclose(win.sample(lat, lon), full.sample(lat, lon), atol=1e-9)
