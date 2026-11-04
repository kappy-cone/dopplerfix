"""End-to-end milestone-4 runs through the CLI: audio analysis -> cascade -> outputs contract."""
import json
import os

import pyogrio
import pytest
import rasterio

import cli
from doppler import export


@pytest.fixture(scope="module")
def scenario_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("scn")
    assert cli.main(["synth", "--out", str(d), "--seed", "0", "--event-times", "150,420", "--duration-s", "600",
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
    assert rj["silence_removed"] >= 1                                  # the cascade decoys hit the silence filter
    assert rj["disk_footprint"]["total_bytes"] < 5_000_000
    assert pyogrio.read_info(run / "results.gpkg", layer="stage_silence")["features"] == s["silence"]
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
