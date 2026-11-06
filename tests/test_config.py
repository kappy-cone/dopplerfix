"""Every tolerance/threshold lives in config.yaml: each parameter dataclass field has a key."""
import dataclasses

import pytest

import cli
from doppler import adsblol, audio, cascade, classify, detect, evaluate, fit, nulltest, realism, ridge, score, synth, tracks

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
    ("realism", realism.RealismParams, set()),
    ("evaluate", evaluate.EvalParams, set()),
    ("diagnostics", nulltest.DiagnosticsParams, set()),
    ("adsblol", adsblol.AdsbLolParams, set()),
]


# keys read by cli.py itself rather than a parameter dataclass
CLI_READ = {"synth": {"scenario_detection"}}     # cli.scenario_detection_cfg


@pytest.mark.parametrize("section,klass,external", SECTIONS, ids=[s[0] for s in SECTIONS])
def test_config_covers_every_parameter(section, klass, external):
    keys = set(CFG[section])
    missing = {f.name for f in dataclasses.fields(klass)} - keys - external
    assert not missing, f"config.yaml [{section}] is missing {sorted(missing)}"
    unknown = keys - {f.name for f in dataclasses.fields(klass)} - {"source", "path", "columns"} - CLI_READ.get(section, set())
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


def _keys(d, prefix=""):
    for k, v in d.items():
        if isinstance(v, dict) and v:
            yield from _keys(v, f"{prefix}{k}.")
        else:
            yield f"{prefix}{k}"


@pytest.mark.parametrize("name", sorted(CFG["variants"]))
def test_variant_overlays_only_touch_existing_keys(name):
    """A typo in an overlay would silently do nothing: every overlay key must exist in the base config."""
    base = set(_keys(CFG))
    over = set(_keys(CFG["variants"][name] or {}))
    assert over <= base, f"variant {name} sets unknown keys {sorted(over - base)}"


def test_variant_resolution():
    v0 = cli.apply_variant(CFG, "V0")
    v1 = cli.apply_variant(CFG, "V1")
    assert v0["variant"] == "V0" and v1["variant"] == "V1"
    assert v0["cascade"]["max_unmatched_events"] == 0 and v0["cascade"]["silence_audibility"] == "hard"
    # V1 is the base file; the overlay merge leaves unrelated keys alone
    assert {k: v for k, v in v1.items() if k != "variant"} == CFG
    assert v0["cascade"]["n_sigma"] == CFG["cascade"]["n_sigma"]
    with pytest.raises(SystemExit):
        cli.apply_variant(CFG, "nope")


def test_config_provenance_reports_overrides(tmp_path):
    """P0.1h: an edited tolerance shows up in config_overrides; a named variant does not."""
    import copy

    import yaml

    same = cli.config_provenance(CFG, "config.yaml")
    assert same["config_overrides"] == {} or same["committed_sha256"] != same["sha256"]
    v0 = cli.apply_variant(CFG, "V0")
    assert "cascade.max_unmatched_events" not in (cli.config_provenance(v0, "config.yaml")["config_overrides"] or {})
    edited = copy.deepcopy(CFG)
    edited["cascade"]["n_sigma"] = 2.5
    p = tmp_path / "edited.yaml"
    p.write_text(yaml.safe_dump(edited))
    prov = cli.config_provenance(edited, p)
    assert prov["config_overrides"]["cascade.n_sigma"] == [CFG["cascade"]["n_sigma"], 2.5]


def _need_calls(path: str) -> set[tuple[str, str]]:
    """(section, key) of every ``need(cfg, "section", "key")`` call in a source file."""
    import ast
    from pathlib import Path

    out = set()
    for node in ast.walk(ast.parse(Path(path).read_text())):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "need"
                and len(node.args) == 3 and all(isinstance(a, ast.Constant) for a in node.args[1:])):
            out.add((node.args[1].value, node.args[2].value))
    return out


def test_every_strict_read_has_a_config_key():
    """cli.need() has no fallback, so each key it reads must be in config.yaml (and in every variant)."""
    calls = _need_calls("cli.py")
    assert len(calls) >= 15, "the AST scan found too few need() calls; has the helper been renamed?"
    for name in [None, *CFG.get("variants", {})]:
        cfg = CFG if name is None else cli.apply_variant(CFG, name)
        missing = sorted(f"[{s}] {k}" for s, k in calls if k not in (cfg.get(s) or {}))
        assert not missing, f"config.yaml{'' if name is None else f' (variant {name})'} lacks {missing}"


def test_need_names_the_missing_key():
    with pytest.raises(KeyError, match=r"\[mic\] is missing 'height_agl_m'"):
        cli.need({"mic": {}}, "mic", "height_agl_m")
    with pytest.raises(KeyError, match=r"\[mic\]"):
        cli.need({}, "mic", "height_agl_m")
