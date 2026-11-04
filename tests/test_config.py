"""Every tolerance/threshold lives in config.yaml: each parameter dataclass field has a key."""
import dataclasses

import pytest

import cli
from doppler import audio, cascade, classify, detect, fit, ridge, score, synth, tracks

CFG = cli.load_config("config.yaml")

# (section, dataclass, fields deliberately supplied from elsewhere)
SECTIONS = [
    ("spectrogram", audio.SpectrogramParams, set()),
    ("spectrogram_jet", audio.SpectrogramParams, set()),
    ("detect", detect.DetectParams, set()),
    ("ridge", ridge.RidgeParams, set()),
    ("fit", fit.FitParams, set()),
    ("classify", classify.ClassifyParams, set()),
    ("tracks", tracks.TrackParams, set()),
    ("synth", synth.SynthParams, {"audible_slant_m", "region_radius_m", "mic_height_m"}),
    ("cascade", cascade.CascadeParams, {"allow_shared_tracks", "max_hypotheses_per_cell"}),  # from score:
    ("score", score.ScoreParams, set()),
]


@pytest.mark.parametrize("section,klass,external", SECTIONS, ids=[s[0] for s in SECTIONS])
def test_config_covers_every_parameter(section, klass, external):
    keys = set(CFG[section])
    missing = {f.name for f in dataclasses.fields(klass)} - keys - external
    assert not missing, f"config.yaml [{section}] is missing {sorted(missing)}"
    unknown = keys - {f.name for f in dataclasses.fields(klass)} - {"source", "path", "columns"}
    assert not unknown, f"config.yaml [{section}] has keys no code reads: {sorted(unknown)}"


def test_config_parses():
    p = cli.event_params(CFG)
    assert p["ridge_jet"].max_down_hz_per_s > p["ridge"].max_down_hz_per_s
    assert p["detect"].jet_bands_hz and p["spec_jet"].window_s < p["spec"].window_s


def test_provisional_thresholds_are_marked():
    text = open("config.yaml").read()
    for key in ("wind_sys_mps", "height_sys_m", "timing_sys_s", "audible_slant_m", "jet_min_broadband_rise_db"):
        assert key in text
    assert text.count("PROVISIONAL") >= 5


def test_no_fixed_percentage_cascade_tolerances():
    assert not {k for k in CFG["cascade"] if k.endswith("_frac")}
