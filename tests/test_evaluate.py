"""doppler/evaluate.py: outcome tiers (U10), failure taxonomy, derived fallback arms, metric helpers."""
import numpy as np
import pytest

from doppler import evaluate as ev
from doppler import score

P = ev.EvalParams()


def test_outcome_tiers():
    assert ev.outcome("unique", 120.0, True, P) == "correct"
    assert ev.outcome("unique", 120.0, None, P) == "correct"           # real data: assignment unknown
    assert ev.outcome("ambiguous", 50.0, True, P) == "abstained"
    assert ev.outcome("inconsistent", 9000.0, False, P) == "abstained"
    assert ev.outcome("none", None, None, P) == "abstained"
    assert ev.outcome("unique", 400.0, True, P) == "wrong"               # near miss, right aircraft
    assert ev.outcome("unique", 1500.0, True, P) == "confident_wrong"
    assert ev.outcome("unique", 400.0, False, P) == "confident_wrong"    # wrong aircraft, confidently
    assert ev.outcome("unique", 150.0, False, P) == "wrong"              # wrong aircraft but within the radius


def test_failure_taxonomy():
    assert ev.failure_class("grid", None, 3000.0, P) == "survival:grid"
    assert ev.failure_class("offset", True, 3000.0, P) == "survival:offset"
    assert ev.failure_class("scored", False, 3000.0, P) == "discrimination"
    assert ev.failure_class("scored", True, 400.0, P) == "bias"
    assert ev.failure_class("scored", True, 50.0, P) is None


def test_reorder_rule_is_fixed_and_threshold_based():
    rows = [{"failure": "bias"}] * 5 + [{"failure": "discrimination"}] * 2 + [{"failure": None}] * 3
    assert ev.reorder_rule(rows).startswith("P1.3")
    rows = [{"failure": "survival:grid"}] * 4 + [{"failure": "bias"}] * 6
    assert ev.reorder_rule(rows).startswith("P3.5")
    rows = [{"failure": "survival:silence"}] * 3 + [{"failure": "bias"}] * 3 + [{"failure": "discrimination"}] * 4
    assert ev.reorder_rule(rows).startswith("Phase 2")


def test_match_and_assignment():
    m = ev.match_events([14.6, 30.0, 45.2], [15.0, 45.0, 80.0], 10.0)
    assert m == [0, 2, None]
    assert ev.assignment_correct(["a", None, "b"], m, ["a", "b", "c"]) is True     # spurious event 1 ignored
    assert ev.assignment_correct(["a", None, "x"], m, ["a", "b", "c"]) is False
    assert ev.assignment_correct(["a"], [None], ["a"]) is None


def test_fallback_arm():
    strict_empty = {"n_candidates": 0, "status": "none", "outcome": "abstained"}
    strict_bad = {"n_candidates": 3, "status": "inconsistent", "outcome": "abstained"}
    robust = {"n_candidates": 9, "status": "unique", "outcome": "correct"}
    assert ev.fallback_arm(strict_empty, robust, "zero_survivors")["outcome"] == "correct"
    assert ev.fallback_arm(strict_bad, robust, "zero_survivors")["outcome"] == "abstained"
    assert ev.fallback_arm(strict_bad, robust, "absolute_fit")["fallback_fired"]
    with pytest.raises(ValueError):
        ev.fallback_arm(strict_bad, robust, "nope")


def test_along_cross_and_hops():
    a, c = ev.along_cross(100.0, 0.0, 90.0)                 # heading east: an east error is along-track
    assert a == pytest.approx(100.0) and c == pytest.approx(0.0, abs=1e-9)
    a, c = ev.along_cross(0.0, 100.0, 90.0)
    assert a == pytest.approx(0.0, abs=1e-9) and abs(c) == pytest.approx(100.0)
    model = np.full(100, 100.0)
    ridge = model.copy()
    ridge[20:30] *= 2.0                                     # hops to the 2nd harmonic
    ridge[60:70] *= 1.5                                     # and to 3/2
    assert ev.harmonic_hops(ridge, model, 0.015) == 2
    assert ev.harmonic_hops(model * 1.004, model, 0.015) == 0


def test_clopper_pearson_and_summary():
    lo, hi = ev.clopper_pearson(20, 20, two_sided=False)
    assert lo == pytest.approx(0.861, abs=1e-3)
    lo2, _ = ev.clopper_pearson(20, 20)
    assert lo2 == pytest.approx(0.832, abs=1e-3)
    rows = [{"outcome": "correct", "status": "unique", "rank1_error_m": 50.0},
            {"outcome": "confident_wrong", "status": "unique", "rank1_error_m": 5000.0, "failure": "discrimination"},
            {"outcome": "abstained", "status": "ambiguous", "rank1_error_m": 300.0, "failure": "bias"}]
    s = ev.summarize(rows)
    assert s["correct"]["k"] == 1 and s["precision_unique"]["rate"] == pytest.approx(0.5)
    assert s["failures"] == {"discrimination": 1, "bias": 1}


def test_ridge_scatter_ref_recovers_white_and_correlated_noise():
    rng = np.random.default_rng(0)
    t = np.arange(0, 120, 0.1)
    curve = 100 * 343 / (343 + 60 * 60 * (t - 60) / np.sqrt(500 ** 2 + (60 * (t - 60)) ** 2))
    # white noise at the decimated scale (0.5 s bins of 5 points: the median scatter is what is measured)
    tt = np.arange(0, 120, 0.5)
    cc = np.interp(tt, t, curve)
    assert score.ridge_scatter_ref(tt, cc + rng.normal(0, 0.3, tt.size)) == pytest.approx(0.3, rel=0.2)
    # AR(1) noise with ρ = 0.6 at the decimated scale
    e = np.zeros(tt.size)
    for i in range(1, tt.size):
        e[i] = 0.6 * e[i - 1] + rng.normal(0, 0.3 * np.sqrt(1 - 0.36))
    assert score.ridge_scatter_ref(tt, cc + e) == pytest.approx(0.3, rel=0.3)


def test_absolute_fit_status():
    p = score.ScoreParams()
    st = score.absolute_fit_status([0.2, np.nan], [0.1, 0.1], p)
    assert not st["inconsistent"] and st["rms_over_ref"][1] is None
    assert score.absolute_fit_status([0.5, 0.1], [0.1, 0.1], p)["inconsistent"]
