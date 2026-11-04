"""Realism layer: ISO 9613-1 values, ADS-B degradation, OpenSky lastposupdate, clean path unchanged."""
import numpy as np
import pandas as pd
import pytest

from doppler import realism as rl
from doppler import synth, tracks

from .conftest import FRAME, START


def test_iso9613_matches_standard_table():
    a = rl.iso9613_alpha_db_per_m(np.array([125, 500, 1000, 2000]), 20.0, 70.0) * 1000
    np.testing.assert_allclose(a, [0.34, 2.80, 4.98, 9.02], rtol=0.03)


def _clean_frame():
    sc = synth.make_scenario(synth.SynthParams(seed=1, duration_s=60, event_rx_times_s=(30.0,)), FRAME, START, 1600.0)
    return sc, tracks.tracks_to_frame(sc.tracks, sc.frame)


def test_adsb_degrader_rate_quantisation_and_bias():
    sc, df = _clean_frame()
    p = rl.RealismParams(adsb_degrade=True, adsb_gap_rate_per_min=0.0, adsb_baro_only_frac_prop=1.0)
    out, info = rl.degrade_adsb(np.random.default_rng(0), df, {s.icao24: "prop" for s in sc.specs}, p)
    g = out[out.icao24 == sc.specs[0].icao24]
    dt = np.diff(pd.to_datetime(g.time_utc).astype("int64").to_numpy() / 1e9)
    assert 0.8 < np.median(dt) < 1.2                                  # ~1 s fixes, not the 0.25 s truth grid
    assert g.geometric_alt_m.isna().all()                             # baro-only GA
    q = 25 * rl.FT
    assert np.allclose(np.round(g.baro_alt_m / q) * q, g.baro_alt_m)  # 25 ft steps
    b = info[sc.specs[0].icao24]["time_bias_s"]
    assert abs(b) <= p.adsb_latency_s and not info[sc.specs[0].icao24]["dropped"]


def test_adsb_drop_never_removes_protected_events():
    sc, df = _clean_frame()
    p = rl.RealismParams(adsb_degrade=True, adsb_drop_prop_frac=1.0)
    ev = {s.icao24 for s in sc.event_specs}
    out, info = rl.degrade_adsb(np.random.default_rng(0), df, {s.icao24: "prop" for s in sc.specs}, p, protect=ev)
    assert set(out.icao24) == ev


def test_opensky_lastposupdate_is_the_position_time():
    df = pd.DataFrame({"time": [100.0, 101.0, 102.0], "icao24": ["abc"] * 3, "lat": [40.0, 40.0, 40.001],
                       "lon": [-105.0] * 3, "geoaltitude": [2000.0] * 3, "lastposupdate": [99.4, 99.4, 101.7]})
    n = tracks.normalize_frame(df)
    assert n["t_s"].tolist() == [99.4, 101.7]                         # stale repeat dropped, true fix times kept


def test_realism_off_is_the_clean_scenario():
    p = synth.SynthParams(seed=2, duration_s=60, event_rx_times_s=(30.0,))
    a = synth.make_scenario(p, FRAME, START, 1600.0)
    b = synth.make_scenario(p, FRAME, START, 1600.0, realism=rl.RealismParams())
    assert np.array_equal(a.audio, b.audio) and b.adsb_frame is None and b.realism is None


def test_realistic_scenario_records_its_departures():
    rp = rl.RealismParams(adsb_degrade=True, c_mismatch_mps=3.0, wind_max_mps=10.0, clock_offset_max_s=5.0,
                          mic_height_range_m=(1.0, 10.0), level_model="shared")
    sc = synth.make_scenario(synth.SynthParams(seed=3, duration_s=60, event_rx_times_s=(30.0,)), FRAME, START,
                             1600.0, 343.4, realism=rp)
    assert sc.adsb_frame is not None and abs(sc.c_true - 343.4) <= 3.0 and sc.stated_start_s != START
    assert 1.0 <= sc.mic_enu[2] + FRAME.h0 - 1600.0 <= 10.0
    ev = sc.aircraft_info[sc.event_specs[0].icao24]
    assert ev["peak_band_snr_db"] >= rp.event_min_snr_db - 1e-6


# ---------------------------------------------------------------------------
# P0.2: independent random streams (paired leave-one-group-out ablations)
# ---------------------------------------------------------------------------
def test_ablation_groups_partition_every_realism_field():
    import dataclasses

    names = [n for g in rl.ABLATION_GROUPS.values() for n in g]
    assert len(names) == len(set(names))
    assert set(names) == {f.name for f in dataclasses.fields(rl.RealismParams)}
    for sub in rl.ABLATION_SUBGROUPS.values():
        assert set(sub) <= set(names)


def _all_on():
    import cli

    return rl.RealismParams.from_dict(cli.load_config("config.yaml")["realism"])


def _draws(sc, kind):
    return {k: v.get(kind) for k, v in sc.aircraft_info.items() if v.get(kind) is not None}


@pytest.mark.parametrize("group", ["adsb", "levels", "source", "path", "scene", "geometry"])
def test_toggling_one_group_does_not_shift_other_groups_draws(group):
    """Every hidden draw of the other groups is identical with ``group`` off, per aircraft."""
    p = synth.SynthParams(seed=7, duration_s=60, event_rx_times_s=(15.0, 45.0), n_background=4)
    on = synth.make_scenario(p, FRAME, START, 1600.0, 343.4, realism=_all_on())
    off = synth.make_scenario(p, FRAME, START, 1600.0, 343.4, realism=rl.without(_all_on(), group))
    common = set(on.aircraft_info) & set(off.aircraft_info)
    assert {s.icao24 for s in on.event_specs} <= common
    if group != "geometry":           # geometry moves events, so non-event placement may legitimately change
        assert set(on.aircraft_info) == set(off.aircraft_info)
        assert np.allclose(on.mic_enu, off.mic_enu)
    if group not in ("source", "levels", "path"):
        src_on, src_off = _draws(on, "source"), _draws(off, "source")
        for k in common & set(src_on) & set(src_off):
            assert src_on[k] == src_off[k], (group, k)
    if group == "levels":             # the level offset is the levels group's own draw; the rest must hold
        for k in common:
            a, b = on.aircraft_info[k]["source"], off.aircraft_info[k]["source"]
            assert {x: y for x, y in a.items() if x != "level_offset_db"} == \
                   {x: y for x, y in b.items() if x != "level_offset_db"}, k
    if group != "geometry":
        kin_on, kin_off = _draws(on, "kinematics"), _draws(off, "kinematics")
        assert all(kin_on[k] == kin_off[k] for k in common if k in kin_on and k in kin_off)
    if group != "adsb":
        for k in common & set(on.adsb_info) & set(off.adsb_info):
            a, b = on.adsb_info[k], off.adsb_info[k]
            assert a.get("time_bias_s") == b.get("time_bias_s") and a.get("baro_offset_m") == b.get("baro_offset_m")
    if group not in ("path",):
        assert on.wind_enu == off.wind_enu and on.c_true == off.c_true
    if group != "geometry":
        assert on.stated_start_s == off.stated_start_s


def test_realistic_scenario_records_hidden_draws():
    p = synth.SynthParams(seed=7, duration_s=60, event_rx_times_s=(15.0, 45.0), n_background=4)
    sc = synth.make_scenario(p, FRAME, START, 1600.0, 343.4, realism=_all_on())
    ev = sc.aircraft_info[sc.event_specs[0].icao24]
    assert {"ramp_frac", "twin_detune", "directivity_db", "reflection"} <= set(ev["source"])
    assert {"climb_mps", "accel_frac", "turn_rate_deg_s"} <= set(ev["kinematics"])
