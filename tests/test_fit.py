"""Per-event parameter recovery.

Milestone-2 acceptance: at 10 dB SNR, v within 5 %, d within 15 %, t0 within 1 s.
That is asserted on straight passes, where the straight-line model is exact.
Turning passes are a known model mismatch (CLAUDE.md "Turns and climbs"): they
are checked against looser, documented bounds and the observed errors printed.
"""
import numpy as np
import pytest

from doppler import audio, detect, fit
from doppler import physics as ph
from doppler.detect import EventWindow
from doppler.ridge import Ridge

from .conftest import make, match


def _ridge_from_model(f0, v, d, t0, noise_hz, seed=0, c=343.0):
    t = np.arange(t0 - 50, t0 + 50, 0.1)
    f = ph.straight_freq_received(t, f0, v, d, t0, c) + np.random.default_rng(seed).normal(0, noise_hz, t.size)
    return Ridge(t_s=t, f_hz=f, excess_db=np.full(t.size, 30.0), noise_hz=noise_hz, band_hz=(40, 900))


@pytest.mark.parametrize("f0,v,d", [(133, 60, 200), (120, 80, 1200), (95, 55, 700), (170, 90, 400)])
def test_fit_recovers_model_parameters(f0, v, d):
    t0 = 100.0
    r = _ridge_from_model(f0, v, d, t0, noise_hz=0.2)
    e = fit.fit_ridge(r, EventWindow(r.t_s[0], r.t_s[-1], (40, 900)))
    assert e.converged
    assert e.v_mps == pytest.approx(v, rel=0.01)
    assert e.d_m == pytest.approx(d, rel=0.03)
    assert e.t0_s == pytest.approx(t0, abs=0.1)
    assert e.f0_hz == pytest.approx(f0, rel=0.003)
    assert e.t_cpa_received_s == pytest.approx(e.t0_s + e.d_m / 343.0)
    for k in fit.PARAM_NAMES:
        assert np.isfinite(e.sigma[k]) and e.sigma[k] > 0


def test_initial_guess_reference_case():
    r = _ridge_from_model(133, 60, 200, 50.0, noise_hz=0.0)
    f0, v, d, t0 = fit.initial_guess(r.t_s, r.f_hz, 343.0)
    assert v == pytest.approx(60, rel=0.05)
    assert f0 == pytest.approx(133, rel=0.01)
    assert d == pytest.approx(200, rel=0.25)
    assert t0 == pytest.approx(50.0, abs=1.0)


def test_correlation_factor():
    rng = np.random.default_rng(0)
    assert fit.correlation_factor(rng.standard_normal(5000)) < 1.2
    smooth = np.convolve(rng.standard_normal(5000), np.ones(20) / 20, mode="same")
    assert fit.correlation_factor(smooth) > 10


def _run_pipeline(sc):
    sp = audio.compute_spectrogram(sc.audio, sc.fs_hz)
    fits = fit.characterize_events(sp, detect.detect_events(sp), c=sc.c)
    return match(fits, sc.truth)


STRAIGHT_SEEDS = [0, 1, 2, 3, 4, 5]


@pytest.mark.slow
@pytest.mark.parametrize("seed", STRAIGHT_SEEDS)
def test_recovery_straight_passes_10db(seed):
    """Acceptance: every flyover detected; v ≤ 5 %, d ≤ 15 %, t0 ≤ 1 s."""
    sc = make(seed, snr_db=10.0, curved_fraction=0.0)
    for tru, e in _run_pipeline(sc):
        assert e is not None, f"event at {tru.t_cpa_received_rel_s:.1f}s not detected"
        print(f"seed {seed}: v {tru.v_mps:.1f}->{e.v_mps:.1f}  d {tru.d_m:.0f}->{e.d_m:.0f}  "
              f"t0 {tru.t0_rel_s:.2f}->{e.t0_s:.2f}")
        assert abs(e.v_mps / tru.v_mps - 1) <= 0.05
        assert abs(e.d_m / tru.d_m - 1) <= 0.15
        assert abs(e.t0_s - tru.t0_rel_s) <= 1.0


@pytest.mark.slow
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_recovery_mixed_scenario_with_turns_10db(seed):
    """Default scenario (~1/3 of passes turn at 0.3–0.8 °/s through CPA).

    The straight-line fit is biased on turns (v up to ~7 % observed), so v gets
    a 10 % bound here; d and t0 still meet the milestone targets.
    """
    sc = make(seed, snr_db=10.0)
    for (tru, e), s in zip(_run_pipeline(sc), sc.specs):
        assert e is not None
        v_tol = 0.05 if s.turn_rate_deg_s == 0 else 0.10
        assert abs(e.v_mps / tru.v_mps - 1) <= v_tol
        assert abs(e.d_m / tru.d_m - 1) <= 0.15
        assert abs(e.t0_s - tru.t0_rel_s) <= 1.0
