"""Golden Stage A2 rows: the "identical results" rule, checked automatically (reports/test_plan.md T2).

Three Stage A2 units are rebuilt from their seeds through the ablation worker (the same in-process path
`cli.py ablate` runs, minus the process pool and the timeout), and every result field of their V0 and V1 rows
must equal the committed rows in reports/data/stageA2_rows.jsonl.gz.

- **Compared:** every row field except provenance and run time (RUN_FIELDS). #17 and #18 moved
  config_sha256 / detection_sha256 with results unchanged, so a hash check would fail without a result change.
- **Floats** must be exactly equal where the geoid grids in config.yaml (data/proj/) are installed, as on the
  compute machine that made the rows. Elsewhere (CI) they must agree to rel 1e-6: CONTEXT.md puts the
  7th-digit differences across machines down to the grids. Discrete fields are always exact.
- **Units** (chosen for coverage, and because each finishes in seconds, far from the 900 s ablation cap, so a
  slow runner cannot turn one into a timeout):
  - all-on C seed 39: V0 status none with three events (the cascade keeps no cell); V1 unique and correct
    (27 m), the full V1 scoring and refinement path.
  - sub:twins C seed 7: V0 inconsistent with 16 candidates (V0 scoring); V1 ambiguous, with speed twins.
  - loo:levels C seed 3: V1 ambiguous with three events, under a different realism config.

**Refresh rule.** When a result-changing change is accepted and a new reference run replaces Stage A2, point
REFERENCE at the new rows (and re-pick units if needed) in the same PR that commits them. Never edit the
reference rows to make this test pass.
"""
import gzip
import json
import math
from pathlib import Path

import pytest

import cli

REFERENCE = Path("reports/data/stageA2_rows.jsonl.gz")
UNITS = [("C", "all", 39), ("C", "sub:twins", 7), ("C", "loo:levels", 3)]
VARIANTS = ("V0", "V1")
RUN_FIELDS = {"git", "config_sha256", "detection_sha256", "seconds", "timings_s"}
REL_TOL = 1e-6


def differences(new, ref, path="", rel=0.0) -> list[str]:
    """Paths where two JSON-like values differ. Floats within ``rel`` (0: exactly equal); everything else exact."""
    if isinstance(ref, bool) or isinstance(new, bool) or ref is None or new is None or isinstance(ref, str):
        return [] if new == ref and type(new) is type(ref) else [f"{path}: {new!r} != {ref!r}"]
    if isinstance(ref, (int, float)) and isinstance(new, (int, float)):
        if isinstance(ref, int) and isinstance(new, int):
            return [] if new == ref else [f"{path}: {new} != {ref}"]
        a, b = float(new), float(ref)
        if a == b or (math.isnan(a) and math.isnan(b)):
            return []
        if rel and math.isfinite(a) and math.isfinite(b) and abs(a - b) <= rel * max(abs(a), abs(b)):
            return []
        return [f"{path}: {a!r} != {b!r}"]
    if isinstance(ref, dict) and isinstance(new, dict):
        out = [f"{path}.{k}: missing in {'new' if k in ref else 'reference'}" for k in sorted(set(ref) ^ set(new))]
        for k in sorted(set(ref) & set(new)):
            out += differences(new[k], ref[k], f"{path}.{k}", rel)
        return out
    if isinstance(ref, list) and isinstance(new, list):
        if len(ref) != len(new):
            return [f"{path}: length {len(new)} != {len(ref)}"]
        return [d for i, (x, y) in enumerate(zip(new, ref)) for d in differences(x, y, f"{path}[{i}]", rel)]
    return [f"{path}: {type(new).__name__} {new!r} != {type(ref).__name__} {ref!r}"]


def geoid_grids_installed(cfg: dict) -> bool:
    g = cli.section(cfg, "geoid")
    d = Path(g.get("grid_dir") or "")
    return bool(g.get("grids")) and all((d / name).is_file() for name in g["grids"])


class _Rows(list):
    """Stands in for the worker's multiprocessing queue."""
    def put(self, item):
        self.append(item)


def test_differences_compares_floats_exactly_or_to_a_tolerance():
    ref = {"a": 1.0, "n": 3, "s": "unique", "x": None, "l": [0.5, {"k": 2.0}], "nan": float("nan")}
    assert differences(json.loads(json.dumps(ref)), ref) == []
    near = {**ref, "a": 1.0 + 1e-9}
    assert differences(near, ref) == [".a: 1.000000001 != 1.0"]
    assert differences(near, ref, rel=REL_TOL) == []
    assert differences({**ref, "a": 1.001}, ref, rel=REL_TOL)
    assert differences({**ref, "n": 4}, ref, rel=REL_TOL) == [".n: 4 != 3"]          # ints never tolerant
    assert differences({**ref, "s": "ambiguous"}, ref, rel=REL_TOL)
    assert differences({**ref, "x": 0.0}, ref) == [".x: 0.0 != None"]
    assert differences({**ref, "l": [0.5]}, ref) == [".l: length 1 != 2"]
    assert differences({k: v for k, v in ref.items() if k != "n"}, ref) == [".n: missing in new"]
    assert differences({**ref, "b": True}, {**ref, "b": 1}) and differences({**ref, "b": 1}, {**ref, "b": True})


def test_golden_units_are_in_the_reference():
    ref = {(r["scenario"], r["config"], r["seed"], r["variant"]) for r in map(json.loads, gzip.open(REFERENCE, "rt"))}
    assert all((*u, v) in ref for u in UNITS for v in VARIANTS)


@pytest.mark.heavy                   # about 10-12 s per unit locally: after merge, not in the gate
@pytest.mark.parametrize("scenario,config,seed", UNITS, ids=[f"{c}-{s}" for _, c, s in UNITS])
def test_rebuilt_rows_equal_the_reference(scenario, config, seed, tmp_path):
    cfg = cli.load_config("config.yaml")
    rel = 0.0 if geoid_grids_installed(cfg) else REL_TOL
    ref = {r["variant"]: r for r in map(json.loads, gzip.open(REFERENCE, "rt"))
           if (r["scenario"], r["config"], r["seed"]) == (scenario, config, seed) and r["variant"] in VARIANTS}
    out = _Rows()
    cli._ablate_worker({"scenario": scenario, "config": config, "seed": seed, "variants": list(VARIANTS),
                        "git": None, "config_path": "config.yaml", "tmp_dir": str(tmp_path / "tmp"),
                        "keep_dir": str(tmp_path / "keep"), "asdesigned_dir": None}, out)
    errors = [r["_error"] for r in out if "_error" in r]
    assert not errors, errors[0]
    rows = {r["variant"]: json.loads(json.dumps(r, default=cli._json_safe)) for r in out if "variant" in r}
    assert set(rows) == set(VARIANTS)
    mode = "exact" if rel == 0.0 else f"rel {rel:g} (geoid grids not installed)"
    for v in VARIANTS:
        new = {k: x for k, x in rows[v].items() if k not in RUN_FIELDS}
        old = {k: x for k, x in ref[v].items() if k not in RUN_FIELDS}
        diff = differences(new, old, rel=rel)
        assert not diff, f"{config} seed {seed} {v} ({mode}): " + "; ".join(diff[:10])
