import numpy as np
import pytest

from doppler import physics as ph

C = 343.0


def test_speed_of_sound():
    assert ph.speed_of_sound(0.0) == pytest.approx(331.3)
    assert ph.speed_of_sound(20.0) == pytest.approx(343.42)
    np.testing.assert_allclose(ph.speed_of_sound(np.array([0.0, 10.0])), [331.3, 337.36])


def test_reference_check():
    """CLAUDE.md: f0=133, v=60, d=200, c=343 -> 161.20 / 113.20 Hz, 6.980 Hz/s."""
    f_pre, f_post = ph.straight_asymptotes(133.0, 60.0, C)
    assert round(f_pre, 2) == 161.20
    assert round(f_post, 2) == 113.20
    assert round(ph.straight_max_rate(133.0, 60.0, 200.0, C), 3) == 6.980

    # Model limits agree with the asymptotes, and the numeric slope at CPA agrees with maxRoC.
    f = lambda t: ph.straight_freq_emission(t, 133.0, 60.0, 200.0, 0.0, C)
    assert f(-1e6) == pytest.approx(f_pre, rel=1e-6)
    assert f(1e6) == pytest.approx(f_post, rel=1e-6)
    h = 1e-4
    assert -(f(h) - f(-h)) / (2 * h) == pytest.approx(6.980, abs=1e-3)


def test_inversions_roundtrip():
    f_pre, f_post = ph.straight_asymptotes(133.0, 60.0, C)
    assert ph.invert_speed(f_pre, f_post, C) == pytest.approx(60.0)
    assert ph.invert_f0(f_pre, f_post) == pytest.approx(133.0)
    rate = ph.straight_max_rate(133.0, 60.0, 200.0, C)
    assert ph.invert_distance(133.0, 60.0, rate, C) == pytest.approx(200.0)
    assert ph.horizontal_offset(500.0, 300.0) == pytest.approx(400.0)


def test_straight_emission_time_inverts_delay():
    v, d, t0 = 75.0, 450.0, 12.0
    te = np.linspace(-200, 200, 401)
    tr = te + ph.straight_range(te, v, d, t0) / C
    np.testing.assert_allclose(ph.straight_emission_time(tr, v, d, t0, C), te, atol=1e-9)


def _straight_track(v, d_h, alt, heading_deg=30.0, t0=1000.0, dt=0.25, half_span=300.0):
    """Straight level track whose CPA to the origin mic is at time t0."""
    psi = np.deg2rad(heading_deg)
    u = np.array([np.sin(psi), np.cos(psi), 0.0])      # along-track unit (ENU)
    n = np.array([np.cos(psi), -np.sin(psi), 0.0])     # perpendicular (right of track)
    t = np.arange(t0 - half_span, t0 + half_span + dt / 2, dt)
    pos = (n * d_h + np.array([0, 0, alt]))[None, :] + (t - t0)[:, None] * v * u[None, :]
    return t, pos


@pytest.mark.parametrize("heading", [0.0, 30.0, 137.0, 290.0])
def test_general_curve_matches_straight_model(heading):
    v, d_h, alt, f0, t0 = 68.0, 350.0, 420.0, 120.0, 1000.0
    mic = np.array([0.0, 0.0, 1.5])
    d = np.hypot(d_h, alt - 1.5)
    t, pos = _straight_track(v, d_h, alt, heading, t0)
    vel = np.tile(np.array([np.sin(np.deg2rad(heading)), np.cos(np.deg2rad(heading)), 0.0]) * v, (t.size, 1))

    # 1) Curve on the track's own samples (analytic and finite-difference velocity).
    for V in (vel, None):
        cur = ph.predicted_curve(t, pos, mic, C, V)
        np.testing.assert_allclose(cur.range_m, ph.straight_range(t, v, d, t0), rtol=1e-12)
        sl = slice(2, -2)  # np.gradient is first-order at the ends
        np.testing.assert_allclose(
            f0 * cur.ratio[sl], ph.straight_freq_emission(t[sl], f0, v, d, t0, C), atol=1e-6
        )
        np.testing.assert_allclose(
            f0 * cur.ratio[sl], ph.straight_freq_received(cur.t_received_s[sl], f0, v, d, t0, C), atol=1e-6
        )

    # 2) Evaluation at arbitrary received times (Newton inversion path used by the scorer).
    tr = np.linspace(t0 - 200, t0 + 200, 977)
    ratio, te, _ = ph.predicted_ratio_at(tr, t, pos, mic, C, vel)
    np.testing.assert_allclose(te, ph.straight_emission_time(tr, v, d, t0, C), atol=1e-6)
    np.testing.assert_allclose(f0 * ratio, ph.straight_freq_received(tr, f0, v, d, t0, C), atol=1e-6)


def test_general_curve_broadcasts_over_mics():
    t, pos = _straight_track(70.0, 300.0, 500.0)
    mics = np.random.default_rng(0).normal(0, 500, size=(4, 5, 3))
    cur = ph.predicted_curve(t, pos, mics)
    assert cur.ratio.shape == (4, 5, t.size)
    tr = np.linspace(900, 1100, 50)
    ratio, te, r = ph.predicted_ratio_at(tr, t, pos, mics)
    assert ratio.shape == te.shape == r.shape == (4, 5, 50)
    # Row (1, 2) agrees with the single-mic call.
    r12, _, _ = ph.predicted_ratio_at(tr, t, pos, mics[1, 2])
    np.testing.assert_allclose(ratio[1, 2], r12, atol=1e-12)


def test_curved_track_received_axis_is_consistent():
    """On a turning, climbing track, both evaluation paths agree with each other."""
    dt = 0.25
    t = np.arange(0, 400, dt)
    w = np.deg2rad(1.5)  # 1.5 deg/s turn
    v = 65.0
    psi = w * (t - 200)
    x = (v / w) * (1 - np.cos(psi)) + 200
    y = (v / w) * np.sin(psi)
    z = 400 + 3.0 * (t - 200)
    pos = np.c_[x, y, z]
    mic = np.array([-150.0, 80.0, 1.5])
    cur = ph.predicted_curve(t, pos, mic)
    tr = cur.t_received_s[40:-40:7]
    ratio, te, _ = ph.predicted_ratio_at(tr, t, pos, mic)
    np.testing.assert_allclose(te, t[40:-40:7], atol=1e-6)
    np.testing.assert_allclose(ratio, cur.ratio[40:-40:7], rtol=1e-4)
