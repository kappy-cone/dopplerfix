"""End-to-end milestone-4 runs through the CLI: audio analysis -> cascade -> outputs contract."""
import json

import numpy as np
import os

import pyogrio
import pytest
import rasterio

import cli
from doppler import export


@pytest.fixture(scope="module")
def scenario_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("scn")
    assert cli.main(["synth", "--out", str(d), "--seed", "0", "--event-times", "150,420", "--duration-s", "600", "--clean",
                     "--cascade-decoys", "3"]) == 0
    return d


@pytest.mark.slow
def test_locate_outputs_contract_and_true_cell(scenario_dir, tmp_path, monkeypatch):
    cfg = cli.load_config("config.yaml")
    cfg["outputs"]["root"] = str(tmp_path / "out")
    cfgp = tmp_path / "config.yaml"
    import yaml
    cfgp.write_text(yaml.safe_dump(cfg))
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "plotly.min.js").write_text("// stub")
    assert cli.main(["locate", "--config", str(cfgp), "--scenario", str(scenario_dir), "--run-id", "t1"]) == 0
    run = tmp_path / "out" / "t1"
    assert os.readlink(tmp_path / "out" / "latest") == "t1"
    layers = {n for n, _ in pyogrio.list_layers(run / "results.gpkg")}
    assert layers == set(export.RESULTS_LAYERS)                       # regional run: grid layer included
    for name in layers:
        assert pyogrio.read_info(run / "results.gpkg", layer=name)["crs"] == "EPSG:4326"
    rj = json.loads((run / "run.json").read_text())
    s = rj["survivors"]
    assert s["grid"] > s["cpa"] >= s["offset"] >= s["silence"] >= 1
    assert rj["truth_eval"]["survived_all_stages"]
    assert rj["truth_eval"]["rank"] == 1 and rj["truth_eval"]["true_cell_assignment_correct"]
    assert rj["truth_eval"]["rank1_error_m"] < 150
    assert rj["human_in_loop"] is False and rj["manual_inputs"] == {}
    assert rj["silence_removed"] >= 1                                  # the cascade decoys hit the silence filter
    assert rj["disk_footprint"]["total_bytes"] < 5_000_000
    assert pyogrio.read_info(run / "results.gpkg", layer="stage_silence")["features"] == s["silence"]
    cands = pyogrio.read_dataframe(run / "results.gpkg", layer="candidates")
    top = cands[cands["rank"] == 1].iloc[0]
    assert cands["score"].notna().all() and np.isfinite(top["f0_e0_hz"]) and top["n_unmatched"] == 0
    with rasterio.open(run / "score.tif") as ds:
        assert ds.crs.to_epsg() == 32613 and ds.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG"
    for f in ("funnel.png", "spectrogram_top.png", "score_hist.png", "viewer.html"):
        assert (run / f).stat().st_size > 0
    assert "../plotly.min.js" in (run / "viewer.html").read_text()

    # second run without the grid layer; clean keeps the newest
    assert cli.main(["locate", "--config", str(cfgp), "--scenario", str(scenario_dir), "--run-id", "t2",
                     "--no-plots"]) == 0
    assert cli.main(["clean", "--config", str(cfgp), "--keep", "1"]) == 0
    assert not run.exists() and (tmp_path / "out" / "t2").exists() and (tmp_path / "out" / "plotly.min.js").exists()


@pytest.mark.slow
def test_locate_from_events_file_matches_detection_and_records_edits(scenario_dir, tmp_path):
    """P0.3 seam: `events --out X.parquet` then `locate --events X.parquet` gives the same ranking as detecting;
    an edited events file is recorded as a manual input (never silently treated as automated)."""
    import yaml

    cfg = cli.load_config("config.yaml")
    cfg["outputs"]["root"] = str(tmp_path / "out")
    cfgp = tmp_path / "config.yaml"
    cfgp.write_text(yaml.safe_dump(cfg))
    ev = tmp_path / "ev.parquet"
    assert cli.main(["events", "--config", str(cfgp), "--audio", str(scenario_dir / "audio.flac"), "--out", str(ev)]) == 0
    assert (tmp_path / "ev.meta.json").exists()
    back = export.read_events(ev)
    assert len(back) >= 2 and back[0].cov.shape[0] >= 4

    base = ["locate", "--config", str(cfgp), "--scenario", str(scenario_dir), "--no-plots"]
    assert cli.main(base + ["--run-id", "det"]) == 0
    assert cli.main(base + ["--run-id", "file", "--events", str(ev)]) == 0
    a = json.loads((tmp_path / "out" / "det" / "run.json").read_text())
    b = json.loads((tmp_path / "out" / "file" / "run.json").read_text())
    for k in ("rank", "rank1_error_m", "rank1_score", "true_cell_assignment"):
        assert a["truth_eval"][k] == b["truth_eval"][k], k
    assert a["survivors"] == b["survivors"]
    assert b["manual_inputs"] == {} and b["events_provenance"]["manual"] is False

    # edit the events file: a changed fitted speed must show up as a manual input
    df = export.read_parquet(ev)
    df.loc[0, "v_mps"] = df.loc[0, "v_mps"] + 1.0
    export.write_parquet(df, ev)
    assert cli.main(base + ["--run-id", "edited", "--events", str(ev)]) == 0
    c = json.loads((tmp_path / "out" / "edited" / "run.json").read_text())
    assert "events_file" in c["manual_inputs"] and "events file edited" in c["manual_inputs"]["events_file"]["reasons"]
