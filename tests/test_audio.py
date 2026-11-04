import numpy as np
import pytest

from doppler import audio


def test_time_prior_parsing():
    tp = audio.parse_time_prior({"mode": "known", "start_utc": "2026-06-15T14:00:00Z"})
    assert tp.start_s == pytest.approx(1781532000.0)
    assert tp.to_absolute(10.0) == pytest.approx(1781532010.0)
    w = audio.parse_time_prior({"mode": "window", "window_utc": ["2026-06-15T13:00:00Z", "2026-06-15T15:00:00"]})
    assert w.window_s == pytest.approx((1781528400.0, 1781535600.0))
    r = audio.parse_time_prior({"mode": "relative"})
    with pytest.raises(ValueError):
        r.to_absolute(1.0)
    with pytest.raises(ValueError):
        audio.TimePrior(mode="known")
    with pytest.raises(ValueError):
        audio.TimePrior(mode="guess")


def test_mix_to_mono():
    x = np.c_[np.ones(10), 3 * np.ones(10)]
    np.testing.assert_allclose(audio.mix_to_mono(x), 2.0)
    np.testing.assert_allclose(audio.mix_to_mono(np.arange(4.0)), np.arange(4.0))


def test_spectrogram_axes_and_tone_frequency():
    fs = 8000
    t = np.arange(20 * fs) / fs
    x = np.sin(2 * np.pi * 123.4 * t) + 0.01 * np.random.default_rng(0).standard_normal(t.size)
    sp = audio.compute_spectrogram(x, fs)
    assert sp.window_s == pytest.approx(1.0)
    assert sp.hop_s == pytest.approx(0.1)
    assert sp.df_hz == pytest.approx(1.0)
    assert sp.f_hz[-1] <= 1000.0
    assert sp.t_s[0] == pytest.approx(0.5)            # first frame centre
    assert sp.power_db.shape == (sp.f_hz.size, sp.t_s.size)
    j = sp.t_s.size // 2
    k = np.argmax(sp.power_db[:, j])
    assert abs(sp.f_hz[k] - 123.4) <= 0.5
