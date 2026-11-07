# ADR-0001: Environment pinning: record versions in each run, no transitive lock

**Status:** Accepted (2026-11-06, by the project owner)
**Date:** 2026-11-06
**Deciders:** project owner
**Audit item:** 6 (reports/tech_debt_audit.md)

## Context
`requirements.txt` pins 16 direct packages. `pip freeze` shows 21 more that are unpinned: affine, attrs,
certifi, cffi, click, contourpy, cycler, execnet, fonttools, iniconfig, kiwisolver, packaging, pillow, pluggy,
pycparser, Pygments, pyogrio, pyparsing, python-dateutil, typing_extensions and tzdata.

The audit worried that unpinned transitive packages could change results. Looking closer, every package that
can change a number is already pinned directly:
- numpy, scipy, pandas and pymap3d are pinned.
- PROJ ships inside the pinned pyproj wheel, GDAL inside rasterio, GEOS inside shapely, and libsndfile inside
  soundfile.

The unpinned ones are plotting (contourpy, kiwisolver, pillow, fonttools), I/O plumbing (pyogrio, affine,
click), test tooling (pluggy, iniconfig, execnet) and certificates and timezones (certifi, tzdata). The one
with a real, if small, effect on results is tzdata, and only for local-time parsing, which the pipeline doesn't
do: internal time is UTC epoch seconds.

What actually limits reproducibility today: rows and run.json record the git commit and the config hash, but
**not the package versions or the Python version**. The cross-checks (U19 → Stage A2, #15) compare rows
"on the same machine" because nothing in a row says which environment produced it. #15 also saw 7th-digit
differences across machines, put down to geoid grids, which no row records either.

## Decision
1. Record the environment in every run.json and ablation row: Python version, the versions of the 16 direct
   packages (`importlib.metadata`), and which geoid grids loaded. Leave out hostnames, paths and hardware (the
   repo is public).
2. Don't add a transitive lock file now. Revisit if a cross-check ever fails because of a transitive package,
   or once a second compute machine produces reference rows.

## Options considered

### A. `pip freeze` from the compute machine as `requirements.lock`, with CI installing from it
| Dimension | Assessment |
|---|---|
| Complexity | Low: one file, one CI line |
| Cost | Every dependency bump becomes a two-file change; platform-only packages (e.g. colorama on Windows) leak into the lock |
| Value | Low: pins nothing that changes results |

### B. A universal lock (`uv pip compile --universal`)
| Dimension | Assessment |
|---|---|
| Complexity | Medium: a new tool in the workflow, markers per platform |
| Cost | Resolves to new versions, not the ones that produced the reference rows |
| Value | Low, for the same reason as A |

### C. Record the environment in each run (chosen), no lock
| Dimension | Assessment |
|---|---|
| Complexity | Low: about 20 lines in `cli.py` (`_git_state`'s neighbour) and a test |
| Cost | A few hundred bytes per row |
| Value | High: the cross-checks can say "same environment" instead of assuming it |

## Trade-off analysis
A lock only helps if transitive versions can move results, and here they can't. Recording versions helps
exactly where the project is fragile: comparing rows across runs, sessions and machines.

## Consequences
- Easier: diagnosing a row mismatch; checking that two runs used the same environment.
- Harder: nothing. Rows gain an `environment` field, and older rows lack it, which readers must tolerate.
- Revisit: when a second machine produces reference rows, or after a transitive-caused mismatch.

## Action items
1. [ ] `cli.environment_state()` → `{"python": ..., "packages": {...}, "geoid_grids": [...]}` in run.json and
   in `ablate_row`.
2. [ ] Test: the field exists and contains no path or hostname.
3. [ ] CONTRIBUTING §4: a cross-check compares `environment` first.
