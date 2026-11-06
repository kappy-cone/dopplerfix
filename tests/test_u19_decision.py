"""reports/experiments/u19/decision.py: the arm-applied validity check (V0)."""
import importlib.util
from pathlib import Path

import cli

_spec = importlib.util.spec_from_file_location(
    "u19_decision", Path(__file__).resolve().parents[1] / "reports/experiments/u19/decision.py")
decision = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(decision)
CFG = cli.load_config("config.yaml")


def _rows(sha):
    return {(v, s): {"outcome": "wrong", **({"detection_sha256": sha} if sha is not None else {})}
            for v in ("V0b", "V1b") for s in range(3)}


def test_arm_applied_requires_the_arms_own_detection_record():
    arm = cli.detection_key(cli.apply_variant(CFG, "V1b"))
    assert decision.arm_applied(_rows(arm), CFG)["ok"]
    assert not decision.arm_applied(_rows(None), CFG)["ok"]                    # rows from before the fix
    pre = cli.detection_key(cli.apply_variant(CFG, "V1-preU19"))
    assert not decision.arm_applied(_rows(pre), CFG)["ok"]                     # pre-U19 detection: arm not applied
    rows = _rows(arm)
    rows[("V1b", 0)] = {"outcome": "timeout"}                                  # timeout rows carry no record
    assert decision.arm_applied(rows, CFG)["ok"]
    assert not decision.arm_applied({}, CFG)["ok"]
