import json

import numpy as np
import pandas as pd
import pyogrio
import pytest
import soundfile as sf

import cli
from doppler import export
from doppler.tracks import TRACK_COLUMNS


@pytest.mark.slow
def test_synth_cli_writes_scenario_and_events_roundtrip(tmp_path, capsys):
    out = tmp_path / "scn"
    assert cli.main(["synth", "--config", "config.yaml", "--out", str(out), "--seed", "3",
                     "--event-times", "60,220", "--duration-s", "300", "--cascade-decoys", "0", "--clean"]) == 0
    for name in ("audio.flac", "audio.json", "tracks.parquet", "truth.json", "truth.gpkg",
                 "diagnostic.png", "aircraft_db.csv"):
        assert (out / name).stat().st_size > 0, name
    assert not list(out.glob("*.wav"))

    x, fs = sf.read(out / "audio.flac")
    assert fs == 8000 and x.shape == (300 * 8000,) and sf.info(out / "audio.flac").subtype == "PCM_24"
    assert json.loads((out / "audio.json").read_text())["mode"] == "known"
    df = export.read_parquet(out / "tracks.parquet")
    assert list(df.columns) == TRACK_COLUMNS
    truth = json.loads((out / "truth.json").read_text())
    assert len(truth["events"]) == 2 and -90 < truth["mic"]["lat"] < 90
    assert [round(e["t_cpa_received_rel_s"], 2) for e in truth["events"]] == [60.0, 220.0]
    assert df["icao24"].nunique() == len(truth["roles"]) > 2          # decoys, twins, background too
    assert set(truth["roles"].values()) <= {"event", "decoy", "twin", "background"}
    layers = {name for name, _ in pyogrio.list_layers(out / "truth.gpkg")}
    assert layers == {"mic_truth", "tracks", "cpa_points"}

    # events subcommand on the written FLAC; Parquet and JSON outputs agree
    capsys.readouterr()
    assert cli.main(["events", "--audio", str(out / "audio.flac"), "--out", str(tmp_path / "ev.json")]) == 0
    assert cli.main(["events", "--audio", str(out / "audio.flac"), "--out", str(tmp_path / "ev.parquet")]) == 0
    ev = json.loads((tmp_path / "ev.json").read_text())
    evp = export.read_parquet(tmp_path / "ev.parquet")
    ridges = export.read_parquet(tmp_path / "ev_ridges.parquet")
    assert len(ev) == len(evp) == 2
    np.testing.assert_allclose(evp["v_mps"], [e["v_mps"] for e in ev], rtol=1e-9)
    assert "t0_utc" in ev[0]
    assert len(ridges[ridges["event"] == 0]) == len(ev[0]["ridge"]["t_rel_s"]) >= ev[0]["n_points"] > 100
