# ADR-0002: Lint in the merge gate: ruff, pyflakes rules only

**Status:** Proposed (2026-11-06; awaiting the project owner's decision)
**Date:** 2026-11-06
**Deciders:** project owner (not yet decided) (new dependency, CLAUDE.md Conventions)
**Audit item:** 9

## Context
No lint or type check runs anywhere. A trial run of ruff 0.15 with its default rules (E4, E7, E9, F) finds 308
issues:
- **Style, 186.** 137 `E702` (several statements on one line with `;`), 21 `E401`, 11 `E741`, 10 `E701`,
  7 `E402`. The compact one-line style is a consistent house style here, especially in `reports/experiments/`
  and the tests.
- **Pyflakes (`F`), 121.** In `doppler/`, `cli.py` and `tests/` there are 15:
  - 9 unused imports;
  - 5 unused variables (`cli.py` 392, 770, 849 and 1645; `tests/test_cascade.py` 224);
  - **1 undefined name: `doppler/export.py:465` uses `Iterator` without importing it.** It only survives
    because `from __future__ import annotations` keeps annotations unevaluated. `typing.get_type_hints` would
    raise on it.
  The other 106 are in `reports/experiments/`, mostly `import *` (F403/F405).

The four unused variables in `cli.py` (`frame`, `ta_rank`, `hp`, `cfg`) were read: all are dead assignments, not
results that were meant to be recorded.

## Decision
Add `ruff` to the gate with `select = ["F"]` (pyflakes only: real errors, no style rules), over `doppler/`,
`cli.py`, `tests/` and `qgis/`. Exclude `reports/experiments/`, which is a record and is already covered for
stale names by `tests/test_experiments_import.py`. Fix the 15 findings in the same PR, reading each unused
variable before deleting it. Pin ruff in `requirements.txt`.

## Options considered

### A. No lint (status quo)
Zero cost, but the undefined name and possible dead code in `run_locate` go unseen.

### B. ruff, `F` only (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Low: `[tool.ruff]` in pyproject.toml, one gate step |
| Cost | One dependency: a single static binary wheel, no transitive dependencies, about 10 MB installed. About 0.1 s per run, well inside the 30 s gate. One cache miss in CI when requirements.txt changes |
| Churn | 15 fixes, 9 of them automatic |

### C. ruff with the default rules (E4/E7/E9/F)
186 style changes that fight the house style, for little bug-finding value.

### D. ruff plus mypy
mypy is a heavier dependency, and it would start with hundreds of findings across numpy-typed code. Revisit
for `physics.py` / `geo.py` alone once B has settled.

### E. pre-commit hooks
They depend on each session's local setup. CI is the one place every session's work passes through.

## Trade-off analysis
Pyflakes rules have almost no false positives and catch the errors that tests miss (names in unexecuted
branches, annotations). Style rules would mostly measure taste. A gate-only check fits the push-triggered CI
(CONTRIBUTING §7).

## Consequences
- Easier: undefined names and dead assignments fail at push, not at run time months later.
- Harder: an `import *` or a deliberately unused name needs `# noqa: F401`.
- Revisit: mypy on the physics and geometry modules.

## Action items
1. [ ] `ruff==0.15.1` in requirements.txt; `[tool.ruff] lint.select = ["F"]`, `extend-exclude = ["reports"]`.
2. [ ] Gate step `python -m ruff check .` before pytest, in shard 0 only.
3. [ ] Fix the 15 findings (the four `cli.py` variables are confirmed dead).
4. [ ] CONTRIBUTING §7: lint is part of the gate.
