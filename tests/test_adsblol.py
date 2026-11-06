"""adsb.lol traces (doppler/adsblol.py, export.iter_archive_json, cli.py adsblol), on hand-made trace files in
the layout readsb's README-json.md documents. No network, no real archive."""
import gzip
import io
import json
import tarfile

import numpy as np
import pandas as pd
import pytest

import cli
from doppler import adsblol as al
from doppler import export, tracks

T0 = 1781532000.0                          # 2026-06-15T14:00:00Z
LAT, LON = 40.0394, -105.2258              # conftest FRAME origin (inside the default search region)


def _trace(icao="a1b2c3", t0=T0, n=20, dt=5.0, lat=LAT, lon=LON, t="C172", **kw):
    pts = []
    for k in range(n):
        ac = {"flight": "N123AB  "} if k == 3 else None
        # offset, lat, lon, baro ft, gs kt, track, flags, baro rate fpm, aircraft, source, geom ft, geom rate
        pts.append([k * dt, lat + 1e-3 * k, lon, 5500 + 10 * k, 100.0, 0.0, 0, 120, ac, "adsb_icao", 5600 + 10 * k, 118])
    return {"icao": icao, "timestamp": t0, "trace": pts, "r": "N123AB", "t": t, **kw}


def test_parse_trace_converts_units_and_picks_altitudes():
    df = al.parse_trace(_trace())
    assert list(df.columns) == al.COLUMNS and len(df) == 20
    r = df.iloc[4]
    assert r["time_utc"] == T0 + 20.0 and r["icao24"] == "a1b2c3"
    assert r["geometric_alt_m"] == pytest.approx(5640 * 0.3048) and r["baro_alt_m"] == pytest.approx(5540 * 0.3048)
    assert r["ground_speed_mps"] == pytest.approx(100 * 1852 / 3600) and r["track_deg"] == 0.0
    assert r["vertical_rate_mps"] == pytest.approx(118 * 0.3048 / 60)       # geometric rate preferred
    # the callsign arrives on point 3 only and is carried forward, stripped
    assert list(df["callsign"][:3]) == ["", "", ""] and set(df["callsign"][3:]) == {"N123AB"}


def test_parse_trace_flags_ground_and_short_records():
    obj = {"icao": "ABC123", "timestamp": T0, "trace": [
        [0.0, LAT, LON, "ground", 12.0, 90.0, 0, None, None, "adsb_icao"],          # on the ground
        [1.0, LAT, LON, 5000, 100.0, 90.0, 8, None, None, "mlat"],                  # flag 8: index 3 is geometric
        [2.0, LAT, LON, 5000, 100.0, 90.0, 1 | 2, -500, None],                      # short (pre-2022), stale, new leg
    ]}
    df = al.parse_trace(obj)
    assert df["icao24"].iloc[0] == "abc123"
    g, m, s = df.iloc[0], df.iloc[1], df.iloc[2]
    assert g["onground"] and np.isnan(g["geometric_alt_m"]) and np.isnan(g["baro_alt_m"])
    assert m["geometric_alt_m"] == pytest.approx(5000 * 0.3048) and np.isnan(m["baro_alt_m"])
    assert s["source"] == "" and s["stale"] and s["new_leg"] and np.isnan(s["geometric_alt_m"])
    assert s["vertical_rate_mps"] == pytest.approx(-500 * 0.3048 / 60)


def test_select_points_by_source_stale_and_anonymous():
    obj = {"icao": "~1a2b3c", "timestamp": T0, "trace": [
        [0.0, LAT, LON, 5000, 100, 0, 0, 0, None, "mlat", 5100],
        [1.0, LAT, LON, 5000, 100, 0, 1, 0, None, "adsb_icao", 5100],
        [2.0, LAT, LON, 5000, 100, 0, 0, 0, None, "other", 5100],
    ]}
    df = al.parse_trace(obj)
    p = al.AdsbLolParams()
    assert list(al.select_points(df, p)["source"]) == ["mlat"]               # stale and "other" dropped
    assert al.select_points(df, al.AdsbLolParams(keep_stale=True))["source"].tolist() == ["mlat", "adsb_icao"]
    assert al.select_points(df, al.AdsbLolParams(keep_anonymous=False)).empty
    # MLAT keeps its position but not its geometric altitude, unless asked
    mlat = al.select_points(df, p)
    assert mlat["geometric_alt_m"].isna().all() and mlat["baro_alt_m"].notna().all()
    assert df["geometric_alt_m"].iloc[0] == pytest.approx(5100 * 0.3048)        # the input is not modified
    kept = al.select_points(df, al.AdsbLolParams(mlat_geometric_alt=True))
    assert kept["geometric_alt_m"].iloc[0] == pytest.approx(5100 * 0.3048)


def test_padded_bbox_and_crop():
    la0, la1, lo0, lo1 = al.padded_bbox(40.0, 40.1, -105.3, -105.2, 11119.5)   # 0.1° of latitude
    assert la0 == pytest.approx(39.9, abs=1e-4) and la1 == pytest.approx(40.2, abs=1e-4)
    assert lo1 - (-105.2) > 0.1                                                  # longitude grows more
    df = al.parse_trace(_trace())
    out = al.crop(df, (LAT, LAT + 0.0055, LON - 0.1, LON + 0.1), T0 + 10, T0 + 60)
    assert out["time_utc"].tolist() == [T0 + 10, T0 + 15, T0 + 20, T0 + 25]


def test_extract_skips_traces_outside_the_window_without_parsing_them():
    far_in_time = _trace("bbbbbb", t0=T0 + 86400)
    far_away = _trace("cccccc", lat=10.0, lon=10.0)
    no_type = _trace("dddddd", t=None)
    bbox = al.padded_bbox(LAT, LAT, LON, LON, 30000.0)
    got = list(al.extract([_trace(), far_in_time, far_away, no_type], bbox, T0, T0 + 3600, al.AdsbLolParams()))
    assert [df["icao24"].iloc[0] for df, _ in got] == ["a1b2c3", "dddddd"]
    assert got[0][1] == {"icao24": "a1b2c3", "typecode": "C172", "registration": "N123AB"} and got[1][1] is None


def test_parsed_traces_feed_the_track_pipeline():
    """The canonical columns go straight into tracks_from_frame (geometric altitude chosen, SI velocity)."""
    df = pd.concat([al.parse_trace(_trace()), al.parse_trace(_trace("eeeeee", lon=LON + 0.01))])
    trs = tracks.tracks_from_frame(df, tracks.TrackParams())
    assert sorted(t.icao24 for t in trs) == ["a1b2c3", "eeeeee"]
    tr = trs[0]
    assert tr.alt_source == "geometric" and tr.callsign == "N123AB"
    assert tr.alt_m[0] == pytest.approx(5600 * 0.3048) and np.linalg.norm(tr.vel_enu[0][:2]) == pytest.approx(51.444, abs=1e-3)


def _archive(tmp_path, objs, n_parts=3):
    """A tar of gzipped trace JSONs (plus a non-trace member), split into parts like the release assets."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for k, o in enumerate(objs):
            data = gzip.compress(json.dumps(o).encode()) if k % 2 == 0 else json.dumps(o).encode()
            ti = tarfile.TarInfo(f"traces/{o['icao'][-2:]}/trace_full_{o['icao']}.json")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        junk = b"not json"
        ti = tarfile.TarInfo("README.txt")
        ti.size = len(junk)
        tf.addfile(ti, io.BytesIO(junk))
    raw = buf.getvalue()
    step = len(raw) // n_parts + 1
    paths = []
    for k in range(n_parts):
        p = tmp_path / f"v2026.06.15-planes-readsb-prod-0.tar.a{'abc'[k]}"
        p.write_bytes(raw[k * step:(k + 1) * step])
        paths.append(p)
    return paths


def test_iter_archive_json_streams_split_parts_gzipped_or_not(tmp_path):
    objs = [_trace(f"a{k:05d}") for k in range(5)]
    parts = _archive(tmp_path, objs)
    got = list(export.iter_archive_json(reversed(parts)))          # order of the arguments does not matter
    assert [o["icao"] for o in got] == [o["icao"] for o in objs]


def test_iter_archive_json_reads_an_open_stream_without_closing_it(tmp_path):
    objs = [_trace(f"a{k:05d}") for k in range(3)]
    raw = b"".join(p.read_bytes() for p in _archive(tmp_path, objs))
    f = io.BytesIO(raw)
    assert [o["icao"] for o in export.iter_archive_json(f)] == [o["icao"] for o in objs]
    assert not f.closed


def test_cli_adsblol_reads_stdin(tmp_path, monkeypatch):
    region = cli.load_region(cli.load_config("config.yaml"))[1]
    c = region.centroid
    raw = b"".join(p.read_bytes() for p in _archive(tmp_path, [_trace(lat=c.y, lon=c.x)], n_parts=2))
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(raw)))
    out = tmp_path / "stdin.parquet"
    assert cli.main(["adsblol", "-", "--start", "2026-06-15T14:00:00", "--end", "2026-06-15T14:30:00",
                     "--out", str(out)]) == 0
    assert len(export.read_parquet(out)) == 20


def test_cli_adsblol_writes_cropped_tracks_and_types(tmp_path):
    region = cli.load_region(cli.load_config("config.yaml"))[1]
    c = region.centroid
    objs = [_trace(lat=c.y, lon=c.x)]
    parts = _archive(tmp_path, objs + [_trace("bbbbbb", t0=T0 + 86400)], n_parts=2)
    out = tmp_path / "out" / "tracks.parquet"
    assert cli.main(["adsblol", *map(str, parts), "--start", "2026-06-15T14:00:00", "--end", "2026-06-15T14:30:00",
                     "--out", str(out)]) == 0
    df = export.read_parquet(out)
    assert set(df["icao24"]) == {"a1b2c3"} and len(df) == 20
    types = pd.read_csv(tmp_path / "out" / "tracks_types.csv")
    assert types.to_dict("records") == [{"icao24": "a1b2c3", "typecode": "C172", "registration": "N123AB"}]
    prepared = cli.load_tracks(cli.load_config("config.yaml"), str(out), str(tmp_path / "out" / "tracks_types.csv"))
    assert len(prepared) == 1 and prepared[0].type_code == "C172"
