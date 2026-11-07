"""Gate test of the per-event pipeline (reports/test_plan.md T1): detect -> ridge -> fit -> classify.

The slow suite covers recovery over many seeds after merge; this keeps one short clip of each kind in the merge
gate, so a break in detect.py / ridge.py / fit.py / classify.py is caught before it reaches main. It runs the
production path (cli.run_events) with config.yaml's values, so it also follows default changes such as U19's
arm b. Bounds are the milestone-2 acceptance bounds used by tests/test_fit.py (v 5 %, d 15 %, t0 1 s).

These are regression checks, not calibration: they never justify moving a provisional threshold.
"""
import math

import numpy as np
import pytest

import cli
from doppler import aircraft_types as at
from doppler import classify, fit

from .conftest import make


@pytest.fixture(scope="module")
def cfg():
    return cli.load_config("config.yaml")


def _events(cfg, classes):
    """A 60 s clip, two straight passes at 18 s and 42 s (received CPA), 10 dB SNR, seed 0."""
    sc = make(0, duration_s=60.0, event_rx_times_s=(18.0, 42.0), event_classes=classes, snr_db=10.0,
              curved_fraction=0.0)
    _, fits = cli.run_events(sc.audio, sc.fs_hz, cfg, cli.speed_of_sound_from(cfg))
    return sc, fits


def _matched(sc, fits):
    """Each truth event with its fit (received CPA within 1 s), and the number of unmatched fits."""
    pairs, used = [], set()
    for tr in sc.truth:
        k = min(range(len(fits)), key=lambda i: abs(fits[i].t_cpa_received_s - tr.t_cpa_received_rel_s))
        assert abs(fits[k].t_cpa_received_s - tr.t_cpa_received_rel_s) <= 1.0, \
            f"event at {tr.t_cpa_received_rel_s:.1f} s not detected"
        pairs.append((tr, fits[k]))
        used.add(k)
    return pairs, len(fits) - len(used)


def _check_fit(tr, e):
    assert abs(e.v_mps / tr.v_mps - 1) <= 0.05
    assert abs(e.d_m / tr.d_m - 1) <= 0.15
    assert abs(e.t0_s - tr.t0_rel_s) <= 1.0
    for k in fit.PARAM_NAMES:
        assert math.isfinite(e.sigma[k]) and e.sigma[k] > 0


def test_two_prop_passes(cfg):
    sc, fits = _events(cfg, ("prop", "prop"))
    pairs, spurious = _matched(sc, fits)
    assert spurious == 0
    for tr, e in pairs:
        _check_fit(tr, e)
        assert e.cls == at.PROP
        assert e.class_features.ridge_band == "prop"
        assert e.class_features.harmonics >= 1


def test_jet_and_prop_pass(cfg):
    """A broadband jet pass runs the jet band's ridge and the broadband-rise branch of classify.

    On a 60 s clip the jet is labelled UNKNOWN, not JET: its broadband rise is about 6 dB against the 12 dB
    threshold (13 dB on 180 s and 600 s clips of the same scenario). The quiet level is a percentile of the clip,
    and a short clip with a long jet pass has little quiet time (CLAUDE.md: never assume quiet frames exist). The
    threshold is provisional (milestone 4.5), so this test pins only what must hold whatever its value: the jet
    is never labelled prop, which would drop every jet track from its candidates. UNKNOWN matches either class.
    """
    sc, fits = _events(cfg, ("jet", "prop"))
    pairs, spurious = _matched(sc, fits)
    assert spurious == 0
    (tj, jet), (tp, prop) = pairs
    _check_fit(tj, jet)
    _check_fit(tp, prop)
    assert prop.cls == at.PROP
    assert jet.class_features.ridge_band == "jet"
    assert jet.class_features.harmonics == 0
    assert jet.cls in (at.JET, at.UNKNOWN)
    assert np.isfinite(jet.class_features.broadband_rise_db)


@pytest.mark.parametrize("band,harmonics,rise,expected", [
    ("prop", 1, float("nan"), at.PROP),       # prop_min_harmonics reached: prop, whatever the broadband
    ("prop", 3, 30.0, at.PROP),               # harmonics win over a loud broadband rise
    ("prop", 0, float("nan"), at.UNKNOWN),    # no harmonic, no rise measured
    ("prop", 0, 11.99, at.UNKNOWN),           # just below the rise threshold
    ("prop", 0, 12.0, at.JET),                # at the threshold: loud, no harmonics
    ("jet", 0, 12.0, at.JET),
    ("jet", 2, 20.0, at.JET),                 # jet band: harmonics don't make it prop
    ("jet", 0, 11.99, at.UNKNOWN),
    ("jet", 0, float("nan"), at.UNKNOWN),
    ("prop", 0, float("inf"), at.UNKNOWN),    # a non-finite rise is never loud
])
def test_classify_decide_table(cfg, band, harmonics, rise, expected):
    p = classify.ClassifyParams.from_dict(cli.section(cfg, "classify"))
    assert p.prop_min_harmonics == 1 and p.jet_min_broadband_rise_db == 12.0, \
        "table written for these config values; update the boundary rows with them"
    assert classify.decide(band, harmonics, rise, p) == expected
