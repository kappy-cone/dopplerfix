# ADR-0003: Parquet engine: stay on fastparquet until pyopensky or pandas 4

**Status:** Proposed (2026-11-06; awaiting the project owner's decision)
**Date:** 2026-11-06
**Deciders:** project owner (not yet decided)
**Audit item:** 13

## Context
pandas 3.1 raises two `Pandas4Warning`s from inside `fastparquet/writer.py`: `future.infer_string`, and
`Series.values` on tz-aware data. Both come through `export.write_parquet`, and our code triggers neither
directly. pandas is pinned `==3.1.0`, so nothing breaks until we choose to bump to pandas 4.

CLAUDE.md already decides the end state: switch to pyarrow and drop fastparquet if a dependency pulls pyarrow
in. `docs/pyopensky_dependency.md` measures that: pyopensky adds about 25 packages and 150–200 MB, nearly all
of it pyarrow. Whether pyopensky is needed at all is open. adsb.lol is the preferred real-track source, and
OpenSky only for history.

Parquet sits on the results path. Every ablation unit writes the scenario's `tracks.parquet` and reads it
back, and the `events --out` / `locate --events` seam round-trips events. So a switch must be checked
bit-identical on rebuilt units, not just by the round-trip test. With pyarrow installed, pandas 3's default
`str` dtype also changes storage, from Python objects to pyarrow.

## Decision
Keep fastparquet now. Switch to pyarrow in **one** PR when the first of these happens:
1. pyopensky is approved (it brings pyarrow; CLAUDE.md says keep one engine);
2. a pandas 4 bump is wanted, and fastparquet still warns or fails on it.

That PR rebuilds about 10 Stage A2 units, and their rows must match on every result field.

## Options considered

### A. Switch to pyarrow now
| Dimension | Assessment |
|---|---|
| Complexity | Low in code: `export.py` lines 39–55, two asserts in `tests/test_export.py` |
| Cost | About 150 MB more in every venv and CI cache, for no functional gain today; a result-path change that needs a bit-identity pass |
| Benefit | No more warnings |

### B. Wait for the trigger (chosen)
Free now. The warnings are noise in test output, and they are already documented.

### C. Silence the warnings in pytest config
It hides the signal that triggers B(2). Not worth it.

## Trade-off analysis
The switch costs the same whenever it happens. Doing it now pays the 150 MB and a verification run for
nothing, and could mean doing it twice if pyopensky later pins a different pyarrow range.

## Consequences
- Easier: no environment churn while the U19 work runs.
- Harder: two warnings stay in test output.
- Revisit: at the pyopensky decision (milestone 4.5 / 7 real data), or a pandas 4 bump.

## Action items
1. [ ] None now. Link this ADR from `docs/pyopensky_dependency.md`.
