import json

import numpy as np
import pandas as pd
import pyogrio
import pytest
import soundfile as sf

import cli
from doppler.tracks import TRACK_COLUMNS


@pytest.mark.slow
def test_synth_cli_writes_scenario_and_events_roundtrip(tmp_path, capsys):
    out = tmp_path / "scn"
    assert cli.main(["synth", "--config", "config.yaml", "--out", str(out), "--seed", "3",
                     "--event-times", "60,220", "--duration-s", "300"]) == 0
    for name in ("audio.wav", "audio.json", "tracks.csv", "truth.json", "truth.gpkg",
                 "events_fit.json", "diagnostic.png", "aircraft_db.csv"):
        assert (out / name).stat().st_size > 0, name

    x, fs = sf.read(out / "audio.wav")
    assert fs == 8000 and x.shape == (300 * 8000,)
    assert json.loads((out / "audio.json").read_text())["mode"] == "known"
    df = pd.read_csv(out / "tracks.csv")
    assert list(df.columns) == TRACK_COLUMNS
    truth = json.loads((out / "truth.json").read_text())
    assert len(truth["events"]) == 2 and -90 < truth["mic"]["lat"] < 90
    assert [round(e["t_cpa_received_rel_s"], 2) for e in truth["events"]] == [60.0, 220.0]
    assert df["icao24"].nunique() == len(truth["roles"]) > 2          # decoys, twins, background too
    assert set(truth["roles"].values()) <= {"event", "decoy", "twin", "background"}
    layers = {name for name, _ in pyogrio.list_layers(out / "truth.gpkg")}
    assert layers == {"mic_truth", "tracks", "cpa_points"}

    # events subcommand on the written WAV reproduces the in-memory fits
    capsys.readouterr()
    assert cli.main(["events", "--audio", str(out / "audio.wav"), "--out", str(tmp_path / "ev.json")]) == 0
    ev = json.loads((tmp_path / "ev.json").read_text())
    ev_synth = json.loads((out / "events_fit.json").read_text())
    assert len(ev) == len(ev_synth) == 2
    np.testing.assert_allclose([e["v_mps"] for e in ev], [e["v_mps"] for e in ev_synth], rtol=1e-6)
    assert "t0_utc" in ev[0]
    assert len(ev[0]["ridge"]["t_rel_s"]) >= ev[0]["n_points"] > 100
