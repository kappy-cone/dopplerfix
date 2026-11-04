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
