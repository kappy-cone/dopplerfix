# Prompt for the next session (session 5, new machine)

Paste the block below as the first message of the new Claude Code session, run from the repo root.

```
Read CLAUDE.md, then HANDOFF.md (start at "START HERE"), before doing anything else. Then read
reports/combined_plan.md (§3 plan, §4 and §7 settled decisions), reports/ablation_pilot.md and
reports/apriori_registry.md. All 17 user decisions are already settled in §7; do not reopen them.

This is a new machine. First set it up and verify:
1. Create the venv from requirements.txt; fetch the geoid grids into data/proj and
   outputs/plotly.min.js as HANDOFF describes (check the sha256).
2. Run the fast tests (pytest -m "not slow"), then the slow ones, and report the results.
3. Create the as-designed worktree: git worktree add ../qgis-asdesigned variants-asdesigned.
4. Tell me this machine's core count and RAM, and re-estimate Stage A wall time from the
   pilot timings in reports/ablation_pilot.md.

Then stop and ask me how to run Stage A, before starting it. Options: run it as is at a suitable
--jobs; lower the 900 s per-run cap; or first profile score.rank_cells (the scoring hot spot;
CLAUDE.md says flag hot spots before using numba or multiprocessing).

Constraints: keep both design variants runnable and compared; never tune provisional values on
synthetic data (registry rule); run Stage A only on a clean, committed tree; commit or push only
when I ask. I'll set up OpenSky credentials myself in pyopensky's own config; if Trino access isn't
granted by the time we need it, fall back to https://opensky-network.org/data/scientific.
```
