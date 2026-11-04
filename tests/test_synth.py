import numpy as np
import pytest

from doppler import physics as ph
from doppler import synth
from doppler.tracks import tracks_from_frame, tracks_to_frame

from .conftest import FRAME, START, make


def test_straight_track_geometry_matches_spec():
    mic = np.array([100.0, -50.0, 1.5])
    spec = synth.TrackSpec("abc123", "T1", t_cpa_rel_s=200.0, heading_deg=70.0, speed_mps=72.0,
                           alt_agl_m=500.0, offset_m=400.0, f0_hz=120.0)
    t = np.arange(0, 400, 0.25)
    enu = synth.build_track_enu(spec, mic, 0.0, t)
    tru = synth.event_truth("abc123", 120.0, t, enu, mic, 343.0, t_near_s=200.0)
    assert tru.t0_rel_s == pytest.approx(200.0, abs=1e-3)
    assert tru.h_offset_m == pytest.approx(400.0, abs=1e-3)
    assert tru.d_m == pytest.approx(np.hypot(400.0, 498.5), abs=1e-3)
    assert tru.v_mps == pytest.approx(72.0, rel=1e-6)
    # positive offset -> mic to the left of the track: cross(velocity, mic - aircraft) > 0
    v = np.array([np.sin(np.deg2rad(70)), np.cos(np.deg2rad(70))])
    rel = mic[:2] - enu[np.argmin(np.abs(t - 200)), :2]
    assert v[0] * rel[1] - v[1] * rel[0] > 0


def test_turning_track_keeps_speed_and_pass_point():
    mic = np.zeros(3)
    spec = synth.TrackSpec("t", "T", 100.0, 0.0, 60.0, 400.0, 300.0, 100.0, turn_rate_deg_s=0.5)
    t = np.arange(0, 300, 0.25)
    enu = synth.build_track_enu(spec, mic, 0.0, t)
    speed = np.linalg.norm(np.gradient(enu[:, :2], t, axis=0), axis=1)
    np.testing.assert_allclose(speed[2:-2], 60.0, rtol=2e-3)
    i = np.argmin(np.abs(t - 100))
    np.testing.assert_allclose(enu[i, :2], [300.0, 0.0], atol=1e-6)


def test_scenario_audio_has_doppler_tone_at_predicted_frequency(scenario0, spec0):
    """Spectrogram peak near each CPA sits on the general-model predicted curve."""
    sc = scenario0
    for enu, s, tru in zip(sc.track_enu, sc.specs, sc.truth):
        t_track = sc.tracks[0].t_s - START  # all tracks share the time grid
        for dt in (-15.0, 0.0, 15.0):
            tq = tru.t_cpa_received_rel_s + dt
            ratio, _, _ = ph.predicted_ratio_at(np.array([tq]), t_track, enu, sc.mic_enu, sc.c)
            f_pred = s.f0_hz * ratio[0]
            j = np.argmin(np.abs(spec0.t_s - tq))
            band = (spec0.f_hz > f_pred - 8) & (spec0.f_hz < f_pred + 8)
            f_peak = spec0.f_hz[band][np.argmax(spec0.power_db[band, j])]
            assert f_peak == pytest.approx(f_pred, abs=1.5)


def test_scenario_is_deterministic_and_tracks_roundtrip(scenario0):
    again = make(0)
    np.testing.assert_array_equal(again.audio, scenario0.audio)
    df = tracks_to_frame(scenario0.tracks, FRAME)
    back = tracks_from_frame(df)
    assert [t.icao24 for t in back] == [t.icao24 for t in scenario0.tracks]
    np.testing.assert_allclose(back[0].enu(FRAME), scenario0.track_enu[0], atol=1e-3)
    np.testing.assert_allclose(back[0].t_s, scenario0.tracks[0].t_s, atol=1e-6)


def test_snr_definition():
    """Tone power at CPA over background-noise power in the noise band equals the requested SNR."""
    p = synth.SynthParams(seed=1, snr_db=7.0, n_tracks=1, broadband_to_tone_db=-np.inf, f0_jitter_frac=0.0)
    sc = synth.make_scenario(p, FRAME, START, ground_elev_m=1600.0)
    n = sc.audio.size
    noise = synth.colored_noise(np.random.default_rng(123), n, sc.fs_hz)
    p_noise = synth.band_power(noise, sc.fs_hz, p.noise_band_hz)
    tru = sc.truth[0]
    tone = synth.flyover_audio(np.random.default_rng(1), sc.specs[0], tru, sc.tracks[0].t_s - START,
                               sc.track_enu[0], sc.mic_enu, np.arange(n) / sc.fs_hz, sc.fs_hz, p_noise, p, sc.c)
    i0 = int(tru.t_cpa_received_rel_s * sc.fs_hz)
    p_tone = np.mean(tone[i0 - sc.fs_hz // 4: i0 + sc.fs_hz // 4] ** 2)
    assert 10 * np.log10(p_tone / p_noise) == pytest.approx(7.0, abs=0.3)
