import numpy as np
import pandas as pd
import pytest

from doppler import aircraft_types as at
from doppler import physics as ph
from doppler import tracks
from doppler.geo import LocalFrame

FRAME = LocalFrame(40.0394, -105.2258, 1600.0)
T0 = 1781532000.0


def _truth(n_s=600, dt=0.25):
    """A turning, climbing aircraft in ENU (smooth truth)."""
    t = np.arange(0, n_s, dt)
    w = np.deg2rad(0.4)
    x = 2000 * np.sin(w * t) / w * 0.04 - 8000 + 70 * t
    y = 1500 * np.sin(w * t)
    z = 2200 + 2.0 * t
    return T0 + t, np.c_[x, y, z]


def _adsb_frame(t, enu, rng, period=1.0, drop=(200, 245), spikes=(100, 400), alt_spike=300):
    """Degrade truth to ADS-B-like fixes: ~1 s jittered cadence, a gap, position and altitude spikes."""
    ts = np.arange(t[0], t[-1], period)
    ts = ts + rng.uniform(-0.1, 0.1, size=ts.size)
    ts = ts[(ts > t[0]) & (ts < t[-1])]
    ts = ts[~((ts - t[0] > drop[0]) & (ts - t[0] < drop[1]))]
    e = np.stack([np.interp(ts, t, enu[:, k]) for k in range(3)], axis=1)
    vel = np.stack([np.interp(ts, t, np.gradient(enu[:, k], t)) for k in range(3)], axis=1)
    lat, lon, h = FRAME.to_geodetic(e)
    for s in spikes:
        i = np.argmin(np.abs(ts - t[0] - s))
        lat[i] += 0.02  # ~2 km jump
    h[np.argmin(np.abs(ts - t[0] - alt_spike))] += 900
    gs = np.hypot(vel[:, 0], vel[:, 1])
    trk = np.degrees(np.arctan2(vel[:, 0], vel[:, 1])) % 360
    return pd.DataFrame({  # OpenSky-style names on purpose
        "time": ts, "icao24": "ABC123", "callsign": "TEST1  ", "lat": lat, "lon": lon,
        "geoaltitude": h, "baroaltitude": h - 30, "velocity": gs, "heading": trk, "vertrate": vel[:, 2],
    })


def test_ingest_clean_split_interpolate():
    t, enu = _truth()
    df = _adsb_frame(t, enu, np.random.default_rng(0))
    raw = tracks.tracks_from_frame(df)
    assert len(raw) == 1 and raw[0].icao24 == "abc123" and raw[0].callsign == "TEST1"
    assert raw[0].alt_source == "geometric" and raw[0].vel_enu is not None
    segs = tracks.prepare_tracks(raw, tracks.TrackParams())
    assert len(segs) == 2                                  # split at the 45 s gap, not interpolated across
    for s in segs:
        assert np.allclose(np.diff(s.t_s), 0.25)
        assert np.allclose(s.t_s / 0.25, np.round(s.t_s / 0.25))   # on the global 0.25 s grid
        truth = np.stack([np.interp(s.t_s, t, enu[:, k]) for k in range(3)], axis=1)
        err = np.linalg.norm(s.enu(FRAME) - truth, axis=1)
        assert err.max() < 5.0, "spikes removed and interpolation close to truth"
        vtruth = np.stack([np.interp(s.t_s, t, np.gradient(enu[:, k], t)) for k in range(3)], axis=1)
        assert np.abs(np.linalg.norm(s.vel_enu, axis=1) - np.linalg.norm(vtruth, axis=1)).max() < 0.5


def test_baro_fallback_and_derivative_velocity():
    t, enu = _truth(200)
    df = _adsb_frame(t, enu, np.random.default_rng(1), drop=(500, 501), spikes=(), alt_spike=150)
    df["geoaltitude"] = np.nan
    df = df.drop(columns=["velocity", "heading"])
    raw = tracks.tracks_from_frame(df)
    assert raw[0].alt_source == "baro" and raw[0].vel_enu is None
    seg = tracks.prepare_tracks(raw)[0]
    assert seg.vel_enu is not None
    vtruth = np.stack([np.interp(seg.t_s, t, np.gradient(enu[:, k], t)) for k in range(3)], axis=1)
    gs, gs_true = np.hypot(seg.vel_enu[:, 0], seg.vel_enu[:, 1]), np.hypot(vtruth[:, 0], vtruth[:, 1])
    assert np.median(np.abs(gs - gs_true)) < 1.0   # derivative of the position fit


def test_trackset_sample_matches_tracks_and_supports_doppler():
    t, enu = _truth(300)
    a = tracks.Track.from_enu("aaaaaa", "A", t, enu, FRAME, vel_enu=None, cls=at.PROP)
    b = tracks.Track.from_enu("bbbbbb", "B", t[400:900], enu[400:900] + [0, 3000, 0], FRAME, cls=at.JET)
    segs = tracks.prepare_tracks([a, b])
    ts = tracks.build_trackset(segs, T0 + 50, T0 + 250, 0.25)
    assert ts.n_tracks == 2 and list(ts.cls) == [at.PROP, at.JET]
    assert ts.active_between(T0 + 50, T0 + 60).tolist() == [True, False]
    q = np.array([[T0 + 120.1, T0 + 130.6]])
    p, v = ts.sample(np.array([[0]]), q)
    exact = segs[0].ecef()
    np.testing.assert_allclose(p[0, 0], [np.interp(T0 + 120.1, segs[0].t_s, exact[:, k]) for k in range(3)], atol=1e-6)
    p_b, _ = ts.sample(np.array([[1]]), np.array([[T0 + 60.0]]))
    assert np.isnan(p_b).all()                                  # outside b's coverage
    # Doppler from the TrackSet (ECEF) equals Doppler from the ENU arrays
    mic_enu = np.array([-1000.0, 400.0, 1.5])
    mic = FRAME.enu_to_ecef(mic_enu)
    tr = np.linspace(T0 + 100, T0 + 200, 20)
    k = 0
    r_set, _, _ = ph.predicted_ratio_at(tr, ts.t_s, ts.xyz[k], mic, track_vel_enu=ts.vel[k])
    r_enu, _, _ = ph.predicted_ratio_at(tr, segs[0].t_s, segs[0].enu(FRAME), mic_enu)
    np.testing.assert_allclose(r_set, r_enu, atol=2e-4)


def test_trackset_rejects_off_grid_tracks():
    t, enu = _truth(50)
    tr = tracks.Track.from_enu("x", "X", t + 0.1, enu, FRAME)
    with pytest.raises(ValueError):
        tracks.build_trackset([tr], T0, T0 + 40, 0.25)
