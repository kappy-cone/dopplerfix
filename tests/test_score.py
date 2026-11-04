"""modeled_ratio (general model on the received axis) against the physics reference implementation."""
import numpy as np
import pytest

from doppler import physics as ph
from doppler import score, tracks
from doppler.geo import LocalFrame

FRAME = LocalFrame(40.0, -105.0, 0.0)
START = 1.7e9


def test_modeled_ratio_matches_physics_predicted_ratio():
    t = np.arange(0, 300, 0.25)
    enu = np.c_[70 * (t - 150), np.full_like(t, 400.0), 600 + 0 * t]
    tr = tracks.Track.from_enu("a", "A", START + t, enu, FRAME)
    v_ecef = np.gradient(tr.ecef(), tr.t_s, axis=0)
    from doppler.geo import enu_basis
    tr.vel_enu = np.einsum("nij,nj->ni", enu_basis(tr.lat_deg, tr.lon_deg), v_ecef)
    ts = tracks.build_trackset([tr], START, START + 300, 0.25)
    mic = np.array([0.0, 0.0, 1.5])
    t_rx = START + np.linspace(60, 240, 200)
    ratio, te, r, ok = score.modeled_ratio(FRAME.enu_to_ecef(mic), 0, ts, t_rx, 343.0)
    ref, te_ref, r_ref = ph.predicted_ratio_at(t_rx - START, t, enu, mic, 343.0)
    assert ok.all()
    np.testing.assert_allclose(te - START, te_ref, atol=2e-3)
    np.testing.assert_allclose(ratio, ref, atol=2e-5)
    # received-time identity
    np.testing.assert_allclose(te + r / 343.0, t_rx, atol=1e-6)


def test_profile_f0_closed_form():
    rho = np.linspace(1.1, 0.9, 50)
    assert score.profile_f0(133.0 * rho, rho) == pytest.approx(133.0)


def test_score_hypotheses_is_milestone_5():
    with pytest.raises(NotImplementedError):
        score.score_hypotheses([], [], None, {}, 0.0, 343.0, score.ScoreParams())
