"""Experiment scripts under reports/experiments/ must keep resolving against the current code.

CI skips pushes that only touch reports/**, and nothing imports these scripts, so a rename in doppler/ or
cli.py could silently break a script that a report needs for reproduction. This parses each script (it runs
nothing) and checks that every name it takes from ``doppler``/``cli`` still exists.
"""
import ast
import importlib
from pathlib import Path

import pytest

SCRIPTS = sorted(Path("reports/experiments").rglob("*.py"))
OURS = ("doppler", "cli")


def _ours(module: str | None) -> bool:
    return bool(module) and (module in OURS or module.startswith("doppler."))


def _exists(module: str, name: str) -> bool:
    m = importlib.import_module(module)
    if hasattr(m, name):
        return True
    try:                                      # a submodule, e.g. ``from doppler import score``
        importlib.import_module(f"{module}.{name}")
        return True
    except ImportError:
        return False


def unresolved(path: Path) -> list[str]:
    """Names a script imports or uses from our modules that do not exist."""
    tree = ast.parse(path.read_text(), filename=str(path))
    aliases: dict[str, str] = {}              # local name -> module it refers to
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if _ours(a.name):
                    aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _ours(node.module):
            for a in node.names:
                if not _exists(node.module, a.name):
                    bad.append(f"from {node.module} import {a.name}")
                elif node.module == "doppler":    # ``from doppler import score``: track uses of score.X
                    aliases[a.asname or a.name] = f"doppler.{a.name}"
    for node in ast.walk(tree):               # attribute uses on an aliased module: cli.run_locate, score.X
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            mod = aliases[node.value.id]
            if mod == "doppler":
                continue                      # ``doppler.score`` style access is covered by the submodule check
            if not hasattr(importlib.import_module(mod), node.attr):
                bad.append(f"{node.value.id}.{node.attr} ({mod})")
    return sorted(set(bad))


def test_scripts_found():
    assert SCRIPTS, "no scripts under reports/experiments/ (run pytest from the repo root)"


@pytest.mark.parametrize("path", SCRIPTS, ids=[str(p.relative_to("reports/experiments")) for p in SCRIPTS])
def test_experiment_script_names_resolve(path):
    assert not unresolved(path), f"{path}: {unresolved(path)}"


def test_checker_catches_a_stale_name(tmp_path):
    p = tmp_path / "stale.py"
    p.write_text("import cli\nfrom doppler import score\nfrom doppler.score import no_such_fn\n"
                 "cli.no_such_cmd()\nscore.also_missing\n")
    assert unresolved(p) == ["cli.no_such_cmd (cli)", "from doppler.score import no_such_fn",
                             "score.also_missing (doppler.score)"]
