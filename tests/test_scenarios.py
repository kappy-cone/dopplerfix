"""Milestone-3 scenario tests: events at chosen offsets across clip lengths, decoys
that only the silence filter can use, prop/jet labelling, chunk-size invariance.
Uses the real config.yaml parameters through cli.event_params."""
import numpy as np
import pytest

import cli
from doppler import aircraft_types as at
from doppler import detect, fit, synth

from .conftest import FRAME, START

CFG = cli.load_config("config.yaml")
THR = CFG["cascade"]["audible_slant_m"]


def scenario(seed, duration, times, classes=None, **kw):
    p = synth.SynthParams(seed=seed, duration_s=duration, event_rx_times_s=times, event_classes=classes,
                          audible_slant_m=THR, **kw)
    return synth.make_scenario(p, FRAME, START, 1600.0)


def run(sc):
    return cli.run_events(sc.audio, sc.fs_hz, CFG, sc.c)


def nearest(fits, t):
    return min(fits, key=lambda e: abs(e.t_cpa_received_s - t)) if fits else None


@pytest.mark.slow
@pytest.mark.parametrize("seed", [0, 1])
def test_long_clip_two_events_at_chosen_offset(seed):
    sc = scenario(seed, 1000.0, (40.0, 960.0), n_decoys_per_event=2, n_background=6)
    assert [round(t.t_cpa_received_rel_s, 2) for t in sc.truth] == [40.0, 960.0]
    _, fits = run(sc)
    e1, e2 = nearest(fits, 40.0), nearest(fits, 960.0)
    assert abs(e1.t_cpa_received_s - 40.0) < 1.0 and abs(e2.t_cpa_received_s - 960.0) < 1.0
    assert abs((e2.t_cpa_received_s - e1.t_cpa_received_s) - 920.0) < 1.0   # measured, never hardcoded
    assert len(fits) == 2, "decoys/background must stay inaudible"


@pytest.mark.slow
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_one_minute_clip_two_overlapping_events(seed):
    """Both flyovers overlap for the whole clip and their ridges cross; both must be recovered,
    with decoys, speed twins and background traffic present (and inaudible)."""
    sc = scenario(seed, 60.0, (15.0, 45.0), n_decoys_per_event=2, n_twins_per_event=2, n_background=10,
                  unknown_type_frac=0.1)
    det, fits = run(sc)
    assert det.windows, "detection must not rely on quiet frames"
    for tru in sc.truth:
        e = nearest(fits, tru.t_cpa_received_rel_s)
        assert e is not None and abs(e.t_cpa_received_s - tru.t_cpa_received_rel_s) < 1.0
        assert abs(e.d_m / tru.d_m - 1) < 0.15
        assert abs(e.v_mps / tru.v_mps - 1) < 0.10   # truncated passes: plateaus only partly observed
        assert e.cls == at.PROP


@pytest.mark.slow
@pytest.mark.parametrize("seed", [0, 1])
def test_prop_and_jet_events_are_labelled(seed):
    sc = scenario(seed, 600.0, (150.0, 420.0), ("prop", "jet"))
    _, fits = run(sc)
    prop, jet = nearest(fits, 150.0), nearest(fits, 420.0)
    assert abs(prop.t_cpa_received_s - 150.0) < 1.0 and prop.cls == at.PROP
    assert abs(jet.t_cpa_received_s - 420.0) < 1.0 and jet.cls == at.JET
    assert jet.class_features.ridge_band == "jet"


def test_decoys_audible_at_target_cell_only():
    sc = scenario(3, 600.0, (150.0, 420.0), n_decoys_per_event=3, n_twins_per_event=2, n_background=8,
                  offset_range_m=(900.0, 1200.0))   # large offsets -> mirror cells far enough for decoys
    placed = [d for d in sc.decoys if d.icao24]
    assert any(d.role == "decoy" for d in placed)
    for d in placed:
        thr = THR[d.cls]
        assert d.min_slant_to_mic_m >= sc.params.inaudible_margin * thr - 1e-6
        if d.role == "decoy":
            assert d.min_slant_to_target_m <= sc.params.decoy_target_slant_frac * thr + 1e-6
    roles = {s.icao24: s.role for s in sc.specs}
    assert sum(r == "twin" for r in roles.values()) >= 1 and sum(r == "background" for r in roles.values()) >= 1
    # decoys that could not be placed are recorded (with their target), not silently dropped
    assert len(sc.decoys) == 2 * 3 + 2 * 2 + 8
    for d in sc.decoys:
        if not d.icao24:
            assert np.isnan(d.min_slant_to_mic_m)


def test_registry_unknowns_and_types():
    sc = scenario(4, 120.0, (60.0,), unknown_type_frac=1.0, n_background=3)
    assert sc.registry.empty
    sc2 = scenario(4, 120.0, (60.0,), ("jet",), unknown_type_frac=0.0)
    lk = at.build_lookup(sc2.registry)
    _, cls = at.classify_icao24([s.icao24 for s in sc2.specs], lk)
    assert cls[0] == at.JET


@pytest.mark.slow
def test_detection_is_chunk_size_invariant():
    sc = scenario(5, 400.0, (100.0, 300.0))
    p = cli.event_params(CFG)
    from dataclasses import replace
    a = detect.detect_events_audio(sc.audio, sc.fs_hz, p["spec"], replace(p["detect"], chunk_s=60.0),
                                   p["classify"].broadband_band_hz, p["spec_jet"])
    b = detect.detect_events_audio(sc.audio, sc.fs_hz, p["spec"], replace(p["detect"], chunk_s=400.0),
                                   p["classify"].broadband_band_hz, p["spec_jet"])
    assert len(a.windows) == len(b.windows) == 2
    for wa, wb in zip(a.windows, b.windows):
        assert abs(wa.start_s - wb.start_s) < 2.0 and abs(wa.end_s - wb.end_s) < 2.0
