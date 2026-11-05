"""Outputs contract: run dirs + latest symlink, pruning, Parquet, score COG, GeoPackage buffering, viewer."""
import json
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import pytest
import rasterio
from shapely.geometry import Point

from doppler import audio, export, plot


def test_run_dirs_latest_symlink_and_prune(tmp_path):
    runs = []
    for k in range(4):
        d = export.new_run_dir(tmp_path, f"r{k}")
        (d / "x.bin").write_bytes(b"\0" * 1000 * (k + 1))
        export.write_run_json(d, {"k": k})
        os.utime(d, (1e9 + k, 1e9 + k))
        runs.append(d)
    link = export.update_latest(tmp_path, runs[1])
    assert link.is_symlink() and os.readlink(link) == "r1"          # relative, not a copy
    (tmp_path / "plotly.min.js").write_text("//")
    expect = sum(export.dir_footprint(runs[k])["total_bytes"] for k in (0, 2))   # data + run.json
    removed, freed = export.prune_runs(tmp_path, keep=1)
    assert {r.name for r in removed} == {"r0", "r2"}                  # the latest target is never removed
    assert freed == expect
    assert (tmp_path / "plotly.min.js").exists() and (tmp_path / "r3").exists() and (tmp_path / "r1").exists()
    rj = json.loads((runs[3] / "run.json").read_text())
    assert rj["disk_footprint"]["files"] == {"x.bin": 4000} and rj["disk_footprint"]["run_json_bytes"] > 0


def test_new_run_dir_is_unique(tmp_path):
    a = export.new_run_dir(tmp_path, "same")
    b = export.new_run_dir(tmp_path, "same")
    assert a != b and a.exists() and b.exists()


def test_parquet_roundtrip_zstd(tmp_path):
    df = pd.DataFrame({"t": pd.to_datetime([0.0, 1.5], unit="s", utc=True), "x": [1.0, 2.0], "s": ["a", "b"]})
    p = export.write_parquet(df, tmp_path / "t.parquet")
    back = export.read_parquet(p, ["x", "s", "missing"])
    assert list(back.columns) == ["x", "s"] and back["x"].tolist() == [1.0, 2.0]
    import fastparquet
    assert fastparquet.ParquetFile(str(p)).schema  # readable; zstd codec
    assert fastparquet.ParquetFile(str(p)).row_groups[0].columns[0].meta_data.codec == 6   # thrift enum: ZSTD


def test_score_raster_is_cropped_cog_in_grid_crs(tmp_path):
    i = np.array([100, 102, 105]); j = np.array([50, 52, 51]); v = np.array([0.5, 1.0, 2.0])
    info = export.write_score_raster(tmp_path / "s.tif", i, j, v, 200.0, "EPSG:32613", "interim")
    assert info["format"] == "COG"
    with rasterio.open(tmp_path / "s.tif") as ds:
        assert ds.crs.to_epsg() == 32613 and ds.shape == (3, 6)
        assert ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
        assert ds.compression.name.upper() == "ZSTD"
        a = ds.read(1)
        assert np.isnan(a).sum() == 18 - 3
        # cell (i, j) centre maps to its pixel
        r, c = ds.index((105 + 0.5) * 200, (51 + 0.5) * 200)
        assert a[r, c] == pytest.approx(2.0)
        assert ds.descriptions[0] == "interim"
    sp = export.write_score_raster(tmp_path / "big.tif", i, j, v, 200.0, "EPSG:32613", "x", max_cells=4)
    assert sp["format"].startswith("sparse")
    with rasterio.open(tmp_path / "big.tif") as ds:
        r, c = ds.index((102 + 0.5) * 200, (52 + 0.5) * 200)
        assert ds.read(1)[r, c] == pytest.approx(1.0)


def test_layer_buffer_appends_and_fixes_layer_set(tmp_path):
    path = tmp_path / "r.gpkg"
    buf = export.LayerBuffer(path, flush_rows=3)
    for k in range(4):
        buf.add("stage_cpa", gpd.GeoDataFrame({"cell_id": [k, k + 10]}, geometry=[Point(k, 0), Point(k, 1)],
                                              crs="EPSG:4326"))
    buf.ensure("stage_silence", gpd.GeoDataFrame({"cell_id": pd.Series([], dtype="int64")},
                                                 geometry=gpd.GeoSeries([], crs="EPSG:4326")))
    assert buf.close() == {"stage_cpa": 8}
    layers = dict(pyogrio.list_layers(path))
    assert set(layers) == {"stage_cpa", "stage_silence"}
    assert pyogrio.read_info(path, layer="stage_cpa")["features"] == 8


def test_viewer_references_shared_plotly_and_is_small(tmp_path):
    from types import SimpleNamespace

    from doppler import cascade as cs

    x = np.random.default_rng(0).normal(size=8000 * 60)
    spec = audio.display_spectrogram(x, 8000, 400.0, max_time_px=300, max_freq_px=100)
    assert spec.power_db.dtype == np.float32 and spec.power_db.shape[1] <= 301 and spec.power_db.shape[0] <= 100
    rt = np.linspace(10, 50, 200)
    ev = cs.ObservedEvent(0, 0, 30.0, 60.0, 500.0, 120.0, np.eye(3), (3.0, 3.0), rt, 120 + 0 * rt, np.ones(200))
    fit = SimpleNamespace(f0_hz=120.0, v_mps=60.0, d_m=500.0, t0_s=28.5, t_cpa_received_s=30.0,
                          sigma={"f0_hz": 0.1, "v_mps": 0.5, "d_m": 5.0, "t0_s": 0.1}, f_pre_hz=142.0, f_post_hz=104.0)
    cand = [{"cell_id": 1, "rank": 1, "score": 0.6, "interim_rank": 2, "lat": 40.0, "lon": -105.0, "dt_s": 0.0,
             "dt_lo_s": -1.0, "dt_hi_s": 1.0, "label": "x",
             "curves": [{"icao24": "abc", "callsign": "A", "f0_hz": 120.0, "t0_s": 0.0, "step_s": 0.25,
                         "ratio": [1.0] * 200 + [None] * 40, "rms_hz": 0.1, "n_missing": 0,
                         "geom": {"v_mps": 60.0, "d_m": 500.0, "t0_rel_s": 28.5, "t_rx_rel_s": 30.0, "alt_m": 2100.0,
                                  "f_pre_hz": 142.0, "f_post_hz": 104.0, "max_roc_hz_s": 2.1}}]}]
    out = tmp_path / "viewer.html"
    mp = {"style": "open-street-map", "n_cells_total": 1, "cells": {"lat": [40.0], "lon": [-105.0], "score": [0.6],
                                                                  "rank": [1]}, "tracks": []}
    plot.write_viewer(out, "run", x, 8000, 1.7e9, [ev], [fit], cand, map_data=mp)
    html = out.read_text()
    assert '"map":{"style":"open-street-map"' in html and "scattermap" in html and "openstreetmap.org/copyright" in html
    assert '<script src="../plotly.min.js"></script>' in html
    for key in ("Compare candidates", "Event fit", "Take snapshot", "BEST = midpoint", "f_pre", "max RoC", "live score"):
        assert key in html
    assert "NaN" not in html.split("const D = ")[1].split(";\n")[0]       # strict JSON payload
    assert len(html) < 300_000


def test_map_layers_carry_cells_and_assigned_tracks_only():
    from types import SimpleNamespace

    from doppler import geo

    K, T = 3, 40
    lat = np.linspace(40.0, 40.02, T)
    xyz = np.stack([geo.geodetic_to_ecef(lat, np.full(T, -105.0 + 0.01 * k), np.full(T, 2000.0)) for k in range(K)])
    xyz[2, 30:] = np.nan                                       # gaps are dropped, not drawn as NaN
    ts = SimpleNamespace(dt_s=0.5, xyz=xyz, icao24=np.array(["aaa", "bbb", "ccc"]), callsign=np.array(["A", "B", "C"]),
                         valid=lambda: np.isfinite(xyz[..., 0]))
    surv = [{"rank": r, "lat": 40.0, "lon": -105.0, "est_lat": 40.001 * r, "est_lon": -105.0, "score": 0.5 * r,
             "tracks": tr} for r, tr in ((1, [0, 2]), (2, [0, -1]), (3, [1, 1]))]
    m = plot.map_layers(surv, ts, n_candidates=2, max_cells=2, step_s=2.0)
    assert m["style"] == "open-street-map" and m["n_cells_total"] == 3
    assert m["cells"]["rank"] == [1, 2] and m["cells"]["lat"] == [40.001, 80.002]   # refined location (est_lat)
    assert m["cells"]["score"] == [0.5, 1.0] and len(m["cells"]["lat"]) == 2
    assert [t["icao24"] for t in m["tracks"]] == ["aaa", "ccc"]             # track 1 belongs only to rank 3
    assert len(m["tracks"][0]["lat"]) == 10 and len(m["tracks"][1]["lat"]) == 8   # every 4th sample of valid ones
    assert not any(np.isnan(m["tracks"][1]["lat"]))
