"""OpenSky state-vector ingest (docs/opensky_ingest.md) on a tiny hand-made sample: no downloads.

The sample follows the documented CSV layout of the scientific state-vector datasets (one snapshot per
aircraft every 10 s; columns as in OPENSKY_STATE_COLUMNS), including the quirks a real file has:
a snapshot before the first position, blank fields, stale snapshots repeated after the last contact,
a squawk with a leading zero, trailing spaces in callsigns, and a baro-only aircraft with on-ground rows.
"""
import io

import numpy as np
import pandas as pd
import pytest

from doppler import tracks

HEADER = ("time,icao24,lat,lon,velocity,heading,vertrate,callsign,onground,alert,spi,squawk,"
          "baroaltitude,geoaltitude,lastposupdate,lastcontact")
assert tuple(HEADER.split(",")) == tracks.OPENSKY_STATE_COLUMNS
SAMPLE = HEADER + """
1656331200,a1b2c3,,,60.0,90.0,0.0,N123AB  ,False,False,False,0123,,,,1656331199.8
1656331210,a1b2c3,40.0394,-105.2400,60.0,90.0,0.0,N123AB  ,False,False,False,0123,2400.0,2430.0,1656331209.6,1656331209.9
1656331220,a1b2c3,40.0394,-105.2329,60.1,90.0,0.0,N123AB  ,False,False,False,0123,2400.0,2430.0,1656331219.6,1656331219.9
1656331230,a1b2c3,40.0394,-105.2259,60.0,90.1,0.0,N123AB  ,False,False,False,0123,2400.0,2430.0,1656331229.6,1656331229.9
1656331240,a1b2c3,40.0394,-105.2188,59.9,90.0,0.0,N123AB  ,False,False,False,0123,2400.0,2430.0,1656331239.6,1656331239.9
1656331250,a1b2c3,40.0394,-105.2118,60.0,90.0,0.0,N123AB  ,False,False,False,0123,2400.0,2430.0,1656331249.6,1656331249.9
1656331260,a1b2c3,40.0394,-105.2118,60.0,90.0,0.0,N123AB  ,False,False,False,0123,2400.0,2430.0,1656331249.6,1656331249.9
1656331270,a1b2c3,40.0394,-105.2118,60.0,90.0,0.0,N123AB  ,False,False,False,0123,2400.0,2430.0,1656331249.6,1656331249.9
1656331200,abcdef,40.0300,-105.2300,30.0,270.0,0.0,,True,False,False,,1650.0,,1656331199.5,1656331199.9
1656331210,abcdef,40.0300,-105.2335,35.0,270.0,0.5,,True,False,False,,1650.0,,1656331209.5,1656331209.9
1656331220,abcdef,40.0301,-105.2400,45.0,270.0,3.0,,False,False,False,7000,1700.0,,1656331219.5,1656331219.9
1656331230,abcdef,40.0302,-105.2470,50.0,270.0,3.0,,False,False,False,7000,1730.0,,1656331229.5,1656331229.9
1656331240,abcdef,40.0303,-105.2540,50.0,270.0,3.0,,False,False,False,7000,1760.0,,1656331239.5,1656331239.9
1656331250,abcdef,40.0304,-105.2610,50.0,270.0,3.0,,False,False,False,7000,1790.0,,1656331249.5,1656331249.9
"""
A_LPU = [1656331209.6, 1656331219.6, 1656331229.6, 1656331239.6, 1656331249.6]


def _csv() -> pd.DataFrame:
    return pd.read_csv(io.StringIO(SAMPLE))


def _by_icao(trs):
    return {t.icao24: t for t in trs}


def test_parse_types_the_csv_quirks():
    raw = _csv()
    assert raw["squawk"].iloc[0] == 123                      # what a CSV reader does to "0123"
    df = tracks.parse_opensky_states(raw)
    assert tracks.is_opensky_states(raw)
    assert df["squawk"].iloc[0] == "0123" and df["squawk"].iloc[8] == "" and df["squawk"].iloc[10] == "7000"
    assert df["onground"].dtype == bool and df["onground"].sum() == 2
    assert df["alert"].dtype == bool and not df["alert"].any()
    assert np.isnan(df["lat"].iloc[0]) and np.isnan(df["lastposupdate"].iloc[0])
    assert df["geoaltitude"].iloc[8:].isna().all()          # B reports barometric altitude only


def test_position_time_stale_snapshots_and_altitude_source():
    trs = _by_icao(tracks.tracks_from_frame(_csv()))
    a, b = trs["a1b2c3"], trs["abcdef"]
    # positions are timed by lastposupdate; the pre-position snapshot and the two stale repeats are dropped
    np.testing.assert_array_equal(a.t_s, A_LPU)
    assert a.callsign == "N123AB" and a.alt_source == "geometric" and np.all(a.alt_m == 2430.0)
    assert a.vel_enu is not None and np.allclose(np.hypot(a.vel_enu[:, 0], a.vel_enu[:, 1]), 60.0, atol=0.2)
    assert b.alt_source == "baro" and b.callsign == "" and len(b.t_s) == 6
    np.testing.assert_allclose(b.alt_m, [1650, 1650, 1700, 1730, 1760, 1790])


def test_onground_fixes_are_kept_and_flagged():
    """No on-ground filter yet (a config decision, docs/opensky_ingest.md §4.3): kept, but flagged."""
    df = tracks.normalize_frame(_csv())
    b = df[df["icao24"] == "abcdef"].dropna(subset=["lat"])
    assert b["onground"].tolist() == [True, True, False, False, False, False]
    assert len(_by_icao(tracks.tracks_from_frame(_csv()))["abcdef"].t_s) == 6
    assert len(_by_icao(tracks.tracks_from_frame(df[~df["onground"]]))["abcdef"].t_s) == 4


def test_normalize_is_idempotent_and_adds_onground():
    once = tracks.normalize_frame(_csv())
    twice = tracks.normalize_frame(once)
    np.testing.assert_array_equal(once["t_s"].to_numpy(), twice["t_s"].to_numpy())
    assert once["onground"].dtype == bool and once["onground"].tolist() == twice["onground"].tolist()
    plain = tracks.normalize_frame(pd.DataFrame({"time": [1.0], "icao24": ["X"], "lat": [40.0], "lon": [-105.0]}))
    assert plain["onground"].tolist() == [False]            # sources without the flag: never on ground


def test_trino_style_types_give_the_same_tracks():
    """Trino / Avro deliver real bools and string squawks; the result must not depend on the reader."""
    df = tracks.parse_opensky_states(_csv())
    typed = df.assign(time=df["time"].astype("int64"))       # flags already bool, squawk already str
    a = _by_icao(tracks.tracks_from_frame(_csv()))
    b = _by_icao(tracks.tracks_from_frame(typed))
    for k in a:
        np.testing.assert_array_equal(a[k].t_s, b[k].t_s)
        np.testing.assert_array_equal(a[k].alt_m, b[k].alt_m)


def test_prepared_track_passes_through_the_reported_positions():
    a = _by_icao(tracks.prepare_tracks(tracks.tracks_from_frame(_csv())))["a1b2c3"]
    i = int(np.argmin(np.abs(a.t_s - 1656331229.5)))         # a grid time near a fix
    assert abs(a.t_s[i] - 1656331229.5) < 1e-6
    assert a.lat_deg[i] == pytest.approx(40.0394, abs=1e-5)
    assert a.lon_deg[i] == pytest.approx(-105.2259, abs=1e-4)


def test_traffic_style_names_are_refused_without_explicit_units():
    df = tracks.normalize_frame(_csv())
    renamed = pd.DataFrame({
        "timestamp": pd.to_datetime(df["t_s"], unit="s", utc=True), "icao24": df["icao24"],
        "latitude": df["lat"], "longitude": df["lon"], "altitude": df["baro_alt_m"],
        "geoaltitude": df["geometric_alt_m"], "groundspeed": df["ground_speed_mps"],
        "track": df["track_deg"], "vertical_rate": df["vertical_rate_mps"],
    })
    with pytest.raises(ValueError, match="units"):
        tracks.tracks_from_frame(renamed)
    mapped = {"altitude": "baro_alt_m", "groundspeed": "ground_speed_mps", "track": "track_deg",
              "vertical_rate": "vertical_rate_mps"}                 # the caller vouches for SI units
    trs = _by_icao(tracks.tracks_from_frame(renamed, aliases=mapped))
    assert set(trs) == {"a1b2c3", "abcdef"}


def test_missing_position_columns_raise():
    with pytest.raises(ValueError, match="not an OpenSky"):
        tracks.parse_opensky_states(_csv().drop(columns=["lat"]))
