"""cli.py ablate: plans, realism config specs, and a disk-safe end-to-end smoke run (slow)."""
import json

import pytest

import cli
from doppler import realism as rl

CFG = cli.load_config("config.yaml")


def test_plans_are_paired_and_sized():
    a = cli.ablation_plan("stageA")
    assert len(a) == 50 + 9 * 20 + 5 + 4 * 10
    seeds = {c: {s for sc, c_, s in a if c_ == c} for _, c, _ in a}
    assert all(set(range(20)) <= seeds["all"] for _ in [0]) and seeds["loo:adsb"] == set(range(20))
    assert len(cli.ablation_plan("pilot")) == 6
    with pytest.raises(SystemExit):
        cli.ablation_plan("nope")


def test_realism_specs():
    on = cli.realism_for(CFG, "all")
    assert on.adsb_degrade and cli.realism_for(CFG, "clean") == rl.RealismParams()
    assert not cli.realism_for(CFG, "loo:adsb").adsb_degrade
    sub = cli.realism_for(CFG, "sub:rpm")
    assert sub.rpm_ramp_prob == 0 and sub.rpm_walk_frac == 0 and sub.adsb_degrade
    assert cli.realism_for(CFG, "set:corridor_frac=1").corridor_frac == 1


@pytest.mark.parametrize("rc, stderr, outcome", [(1, "no events detected\n", "abstained"),
                                                  (1, "Traceback ...\nValueError: boom\n", "error")])
def test_asdesigned_no_events_is_an_abstention_not_an_error(tmp_path, monkeypatch, rc, stderr, outcome):
    import shutil
    import subprocess

    code = tmp_path / "asdesigned"
    code.mkdir()
    shutil.copy("config.yaml", code / "config.yaml")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, rc, "", stderr))
    unit = {"scenario": "C", "config": "all", "seed": 0, "asdesigned_dir": str(code), "timeout_s": 900.0,
            "config_path": "config.yaml"}
    (tmp_path / "tmp").mkdir()
    row = cli._asdesigned_row(unit, "V1-asdesigned", tmp_path / "sc", tmp_path / "tmp")
    assert row["outcome"] == outcome and row["status"] == "none"
    if outcome == "abstained":           # matches the in-process arms' row when run_locate returns None
        assert row["failure"] == "survival:none" and row["n_candidates"] == 0 and row["n_events"] == 0


@pytest.mark.slow
def test_ablate_smoke_writes_rows_and_cleans_up(tmp_path):
    out = tmp_path / "abl"
    assert cli.main(["ablate", "--plan", "smoke", "--variants", "V0,V1", "--out", str(out), "--jobs", "1",
                     "--allow-dirty"]) == 0
    rows = [json.loads(x) for x in (out / "rows.jsonl").read_text().splitlines()]
    assert {r["variant"] for r in rows} == {"V0", "V1"}
    assert all(r["outcome"] in ("correct", "abstained", "wrong", "confident_wrong") for r in rows)
    assert not (out / "tmp").exists()                           # scenarios deleted
    keep = out / "keep" / "C_clean_s0"
    assert (keep / "truth.json").exists() and (keep / "events.parquet").exists()
    assert (out / "summary.md").exists() and "V0->V1 (zero_survivors)" in (out / "summary.md").read_text()
    # resumable: a second call has nothing left to do and keeps the rows
    assert cli.main(["ablate", "--plan", "smoke", "--variants", "V0,V1", "--out", str(out), "--allow-dirty"]) == 0
    assert len((out / "rows.jsonl").read_text().splitlines()) == 2


def test_summary_fills_detection_from_kept_files(tmp_path):
    import pandas as pd

    from doppler import evaluate as ev
    from doppler import export

    keep = tmp_path / "keep" / cli._unit_tag("C", "loo:path", 6)
    keep.mkdir(parents=True)
    (keep / "truth.json").write_text(json.dumps({"events": [{"t_cpa_received_rel_s": 15.0},
                                                            {"t_cpa_received_rel_s": 45.0}]}))
    export.write_parquet(pd.DataFrame({"t_cpa_received_rel_s": [14.8, 30.0]}), keep / "events.parquet")
    rows = [{"scenario": "C", "config": "loo:path", "seed": 6, "variant": "V1-asdesigned"},   # no counts recorded
            {"scenario": "C", "config": "loo:path", "seed": 6, "variant": "V1", "truth_events_detected": 2}]
    filled, mismatched = cli._fill_detection(rows, tmp_path, ev.EvalParams())
    assert (filled, mismatched) == (1, 1)                    # the recorded count is kept, the disagreement counted
    assert rows[0]["truth_events_detected"] == 1 and rows[0]["n_truth_events"] == 2
    assert rows[1]["truth_events_detected"] == 2 and rows[1]["n_truth_events"] == 2


def test_paired_tests_pair_by_seed():
    rows = []
    for s in range(6):
        rows.append({"scenario": "C", "config": "all", "seed": s, "variant": "V0", "rank1_error_m": 900.0 + s})
        rows.append({"scenario": "C", "config": "all", "seed": s, "variant": "V1", "rank1_error_m": 100.0 + s,
                     "rank1_assignment_correct": True})
        rows.append({"scenario": "C", "config": "loo:adsb", "seed": s, "variant": "V1", "rank1_error_m": 100.0 + s})
    from doppler import evaluate as ev

    out = {p["label"]: p for p in cli._paired_tests(rows, ev.EvalParams())}
    t = out["V1 vs V0 (all)"]["tests"]
    assert t["rank1_error_m"]["n"] == 6 and t["rank1_error_m"]["median_diff"] == -800.0
    assert t["rank1_accuracy_sign"]["a_only"] == 6
    assert out["V1: loo:adsb vs all"]["tests"]["rank1_error_m"]["n_nonzero"] == 0


def test_detection_key_follows_detection_sections_only():
    base = cli.detection_key(CFG)
    assert (CFG.get("detect") or {}).get("long_window_s") == 3.0          # arm b is the default since U19
    for v in ("V0", "V1", "V0c", "V1c", "V0b", "V1b"):        # cascade / scoring overlays, or arm b = default
        assert cli.detection_key(cli.apply_variant(CFG, v)) == base
        assert cli.events_file_name(cli.apply_variant(CFG, v), CFG) == "events.parquet"
    for v in ("V0-preU19", "V1-preU19"):                       # detect.long_window_s null: own detection
        assert cli.detection_key(cli.apply_variant(CFG, v)) != base
    assert (cli.detection_key(cli.apply_variant(CFG, "V0-preU19"))
            == cli.detection_key(cli.apply_variant(CFG, "V1-preU19")))
    assert cli.events_file_name(cli.apply_variant(CFG, "V1-preU19"), CFG).startswith("events_")


def test_variant_fits_detects_under_each_variants_own_config(tmp_path, monkeypatch):
    """Regression: the ablation driver once detected with the base config only and shared those fits with every
    variant, so a detect: overlay (V1b / V0b) never reached detection."""
    seen = []

    def fake_run_events(x, fs, cfg, c):
        seen.append((cfg.get("detect") or {}).get("long_window_s"))
        return None, []

    monkeypatch.setattr(cli, "run_events", fake_run_events)
    cache = {}
    for v in ("V1", "V0", "V1-preU19", "V0-preU19"):
        cli.variant_fits(cache, cli.apply_variant(CFG, v), CFG, [0.0], 8000, tmp_path)
    assert seen == [3.0, None]                                 # once for V1 / V0, once for the pre-U19 pair
    assert all((tmp_path / name).exists() for name in cache)
    assert "events.parquet" in cache and len(cache) == 2


def test_summary_reads_each_variants_own_events_file(tmp_path):
    import pandas as pd

    from doppler import evaluate as ev
    from doppler import export

    keep = tmp_path / "keep" / cli._unit_tag("C", "all", 3)
    keep.mkdir(parents=True)
    (keep / "truth.json").write_text(json.dumps({"events": [{"t_cpa_received_rel_s": 15.0},
                                                            {"t_cpa_received_rel_s": 45.0}]}))
    export.write_parquet(pd.DataFrame({"t_cpa_received_rel_s": [14.8]}), keep / "events.parquet")
    export.write_parquet(pd.DataFrame({"t_cpa_received_rel_s": [14.8, 45.3]}),
                         keep / cli.events_file_name(cli.apply_variant(CFG, "V1-preU19"), CFG))
    rows = [{"scenario": "C", "config": "all", "seed": 3, "variant": "V1-preU19"},   # e.g. timeout rows: no counts
            {"scenario": "C", "config": "all", "seed": 3, "variant": "V1"}]
    assert cli._fill_detection(rows, tmp_path, ev.EvalParams(), CFG) == (2, 0)
    assert rows[0]["truth_events_detected"] == 2 and rows[1]["truth_events_detected"] == 1
