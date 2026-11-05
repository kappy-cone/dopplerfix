import numpy as np
import pytest

from doppler import audio, detect, ridge
from doppler import physics as ph


def _chirp_audio(f0=140.0, v=70.0, d=600.0, t0=60.0, dur=120.0, fs=4000, snr_bin_amp=0.5, harmonics=(1.0, 0.5)):
    """Exact straight-pass Doppler tone (phase at emission time, 1/r amplitude) in white noise."""
    t = np.arange(int(dur * fs)) / fs
    te = ph.straight_emission_time(t, v, d, t0)
    env = d / ph.straight_range(te, v, d, t0)
    x = env * sum(a * np.sin(2 * np.pi * k * f0 * te) for k, a in enumerate(harmonics, start=1))
    rng = np.random.default_rng(3)
    return x + rng.standard_normal(t.size) * (1 / snr_bin_amp), fs


def test_detect_finds_event_and_respects_manual_windows():
    x, fs = _chirp_audio()
    sp = audio.compute_spectrogram(x, fs)
    w = detect.detect_events(sp, detect.DetectParams(min_duration_s=10))
    assert len(w) == 1
    assert w[0].start_s < 60.0 < w[0].end_s
    m = detect.detect_events(sp, detect.DetectParams(manual_windows=({"start_s": 5, "end_s": 50, "band_hz": [80, 300]},),
                                                     min_duration_s=10))
    assert any(e.manual and e.band_hz == (80, 300) for e in m)


def test_detect_on_pure_noise_finds_nothing():
    x = np.random.default_rng(1).standard_normal(120 * 4000)
    sp = audio.compute_spectrogram(x, 4000)
    assert detect.detect_events(sp) == []


def test_ridge_follows_fundamental_on_exact_chirp():
    f0, v, d, t0 = 140.0, 70.0, 600.0, 60.0
    x, fs = _chirp_audio(f0, v, d, t0)
    sp = audio.compute_spectrogram(x, fs)
    w = detect.EventWindow(5.0, 115.0, (40.0, 900.0))
    r = ridge.extract_ridge(sp, w)
    assert r is not None and len(r) > 500
    truth = ph.straight_freq_received(r.t_s, f0, v, d, t0)
    err = r.f_hz - truth
    assert np.median(np.abs(err)) < 0.3
    assert r.subharmonic == 1
    assert r.noise_hz < 0.5


def test_ridge_on_second_harmonic_flags_subharmonic():
    f0, v, d, t0 = 110.0, 70.0, 600.0, 60.0
    x, fs = _chirp_audio(f0, v, d, t0, harmonics=(0.5, 1.0))  # 2nd harmonic dominates
    sp = audio.compute_spectrogram(x, fs)
    r = ridge.extract_ridge(sp, detect.EventWindow(5.0, 115.0, (40.0, 900.0)))
    truth2 = ph.straight_freq_received(r.t_s, 2 * f0, v, d, t0)
    assert np.median(np.abs(r.f_hz - truth2)) < 0.5
    assert r.subharmonic == 2


# ---------------------------------------------------------------------------
# U19 detection arms (plan §8.2): each finds a weak signal of its kind that the base statistic misses, adds no
# detection on pure noise, and is off by default
# ---------------------------------------------------------------------------
SP4K = audio.SpectrogramParams(4000, 1.0, 0.9, 1900.0)
BASE = detect.DetectParams(min_duration_s=10, broadband_trigger=False)


def _n_windows(x, fs, **arm):
    from dataclasses import replace

    return len(detect.detect_events_audio(x, fs, SP4K, replace(BASE, **arm)).windows)


def test_detection_arms_are_off_by_default():
    p = detect.DetectParams()
    assert not p.harmonic_stat and p.long_window_s is None and p.low_band_hz is None
    assert p.prop_bands == p.bands_hz


@pytest.mark.parametrize("arm", [{"harmonic_stat": True}, {"long_window_s": 3.0}, {"low_band_hz": (20.0, 100.0)}])
def test_detection_arms_find_nothing_in_pure_noise(arm):
    for s in (1, 2, 3):
        assert _n_windows(np.random.default_rng(s).standard_normal(120 * 4000), 4000, **arm) == 0


def test_harmonic_arm_finds_a_weak_harmonic_stack():
    x, fs = _chirp_audio(f0=150.0, snr_bin_amp=0.36, harmonics=(1.0, 1.0, 1.0))   # every Doppler tone above 100 Hz
    assert _n_windows(x, fs) == 0 and _n_windows(x, fs, harmonic_stat=True) == 1


def test_long_window_arm_finds_a_slow_far_prop():
    x, fs = _chirp_audio(f0=140.0, v=40.0, d=3000.0, snr_bin_amp=0.3, harmonics=(1.0,))
    assert _n_windows(x, fs) == 0 and _n_windows(x, fs, long_window_s=3.0) == 1


def test_low_band_arm_finds_a_weak_tone_below_100_hz():
    x, fs = _chirp_audio(f0=70.0, v=50.0, d=800.0, snr_bin_amp=0.34, harmonics=(1.0,))
    assert _n_windows(x, fs) == 0 and _n_windows(x, fs, low_band_hz=(20.0, 100.0)) == 1


def test_harmonic_noise_baseline_is_deterministic_and_matches_noise():
    f = tuple(np.arange(20.0, 921.0, 1.0))
    a = detect.harmonic_noise_baseline_db(f, 40.0, 900.0, 3)
    detect._HARM_THEORY.clear()
    assert detect.harmonic_noise_baseline_db(f, 40.0, 900.0, 3) == a
    rng = np.random.default_rng(7)
    Z = 10 * np.log10(rng.exponential(size=(len(f), 300)) / np.log(2))
    assert detect._harmonic_rows(Z, np.asarray(f), 40.0, 900.0, 3).mean() == pytest.approx(a, abs=0.2)
