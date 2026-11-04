# Experiment scripts behind the session-4 reports

These are the scripts (plus small configs, logs and text results) that the reviews cite. Most of the
scenarios and runs they generated (about 30 MB) were not kept; they can be regenerated.

| Folder | Cited by |
|---|---|
| `skeptic_physics/` | `../skeptic_ours_physics_scoring.md` |
| `skeptic_system/` | `../skeptic_ours_system_validation.md` |
| `skeptic_theirs/` | `../skeptic_theirs_doppler_txt.md` |
| `designer/` | `../combined_plan.md` (D-1 … D-5) |
| `reviewer/` | `../plan_review.md` |

## Before re-running
- **Code version.** The scripts were run against the code at commit `388832d`, before Phase 0. Phase 0
  (`2b83ad2`) changed the realistic-scenario random streams (P0.2) and the cascade enumeration (P0.1-i).
  Realistic numbers will not reproduce exactly on later code. To reproduce, check out `388832d` or tag
  `variants-asdesigned` (same code as `388832d`), e.g. in a worktree.
- **Paths.** Some scripts and configs hard-code the session's temporary scratch directory
  (`/private/tmp/.../scratchpad/...`). Edit them to a local folder first.
- **How to run.** From the repo root: `.venv/bin/python reports/experiments/<folder>/<script>.py`. They import
  `doppler.*`. `skeptic_theirs/calc.py` is standalone.
