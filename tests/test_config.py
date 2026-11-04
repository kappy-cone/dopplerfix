"""Every tolerance/threshold lives in config.yaml: each parameter dataclass field has a key."""
import dataclasses

import pytest

import cli
from doppler import audio, classify, detect, fit, ridge, synth, tracks

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
