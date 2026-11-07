# HANDOFF: current state

Read CLAUDE.md first; it is authoritative for methodology and conventions. This file is the **current state
only**: where the work stands, what is in flight, what waits for the owner, and what is fragile. Earlier
session logs (sessions 3–6: validation tables, the session-3 decisions, the realism layer's design) are in
[docs/history.md](docs/history.md), unchanged. Update this file at the end of each session: replace what
changed and move what is no longer current into the history.

*Last updated: 2026-11-07 (session 9, laptop). Main at `46b1d05`.*

## Where things stand
- **Milestones.** 1–5 are done (on synthetic data). 6 is partly done: exports, the QGIS template, and a Map tab
  on an OpenStreetMap basemap in `viewer.html` (prototype, [docs/osm_viewer.md](docs/osm_viewer.md)).
  4.5 has a written field-recording protocol ([docs/field_recordings.md](docs/field_recordings.md)) and no
  recordings yet. 7–9 are not started.
- **The plan.** Work follows `reports/combined_plan.md` §8 (amendment after Stage A): Phase 1 arms, U19
  detection alongside them, then real data. Phase 1 status:
  - P1.1 (calibrated likelihood) and P1.4 (ambiguity v2) were built as arms V1c / V0c. They failed the U18
    acceptance test (rank-1 accuracy 34 → 25), and stay comparison arms.
  - P1.2, P1.3, P1.5 and P1.6 are not built (evaluate.py has hooks only).
- **Detection (U19).**
  - Arm b, the 3 s long-window detection rows, was accepted and is the default (`detect.long_window_s: 3.0`).
  - Arm h1g, the guarded harmonic hand-off: neither registered screen rules it out. As built, though, it
    would fail arm b's spurious-event bound, and end to end it gains nothing (19 vs 18 / 50). A decision run
    needs the owner's call ([reports/u19.md](reports/u19.md), end).
- **Reference rows: Stage A2** (`reports/data/stageA2_rows.jsonl.gz`; [reports/stageA.md](reports/stageA.md)
  §A2) are the current baseline: V0 / V1 under the post-U19 defaults, 275 units. Every later arm is paired
  against them.

  | V1, Stage A2 | value |
  |---|---|
  | all-on C, seeds 0–49: rank-1 accuracy (status ignored) | 18 / 50 |
  | all-on C: correct (within 200 m and claimed unique) / confident-wrong | 10 / 5 |
  | all-on C: detection recall | 82 / 100 truth events |
  | other configs pooled: rank-1 accuracy | 84 / 220 |
  | main failure class, V1 all-on | discrimination, 16 of 32 classified failures (50 %) |

- **Code health (session 9).** [reports/tech_debt_audit.md](reports/tech_debt_audit.md) scored 13 items, and
  the Phase 1 quick wins are merged (#18): strict config reads (`cli.need`), a gate test that experiment
  scripts still resolve, and the transcript out of the tree. [CONTEXT.md](CONTEXT.md) records the
  performance history and its rules. [docs/adr/](docs/adr/README.md) holds six ADRs for the open items.
  [reports/test_plan.md](reports/test_plan.md) is the testing strategy (T1–T7). T1, a gate test of the
  per-event pipeline (#21), and T2, golden Stage A2 rows rebuilt and compared after merge (#22), are merged.
  ADRs 0001–0005 are accepted (#24). The US-wide benchmark design (milestone 7) is a proposal in
  [docs/design_uswide_benchmark.md](docs/design_uswide_benchmark.md) (#27), with owner decisions D1–D5.
- **Two-stage refinement arm V1k** (#26) is merged and registered with its decision rule, before any
  measurement:
  - it sets `score.refine_lbfgs_top_k: 50`, so only the best 50 refined cells get L-BFGS-B;
  - the verdict script is `reports/experiments/two_stage_decision.py`;
  - its decision run on the compute machine, from clean main, is next.

## In flight (open PRs)
| PR | What | Where it runs |
|---|---|---|
| #23 | Code-review fixes: missing vertical rate filled from altitude (default, synthetic results unchanged); `cascade.silence_scope` (arm V1s) and `score.refine_order` (arm V1r), off by default. **Conflicts with main since #26** (`config.yaml`, `doppler/score.py`, the registry); needs a rebase. Both change which cells `rank_cells` refines | laptop session |
| #7 | Old session-7 HANDOFF update; main has since moved past it, and this file replaces it | can be closed |

## Waiting for the owner
Each item is the owner's call; sessions don't decide them.
1. **ADR-0006** (lockstep centre scoring; [docs/adr/](docs/adr/README.md)) is still Proposed. 0001–0005 were
   accepted on 2026-11-06.
2. **Code-review arms V1s / V1r (#23):** set a screen and decision rule before the first measurement.
   V1s is the plan's **R2 "per-hypothesis silence"** (plan §8.4 screens it on scene, levels and adsb, plus
   all-on and clean).
3. **U19 arm h1g:** whether to run a decision run (see above).
4. **Plan re-ordering:** discrimination is 50 % of V1 all-on failures in Stage A2, above the §3.0 40 % line.
   The rule was written for Stage A, so whether it applies is the owner's call (stageA.md §A2 reading).
5. **Config items:**
   - `aircraft_types.type_table_path: data/doc8643_types.csv`, for real tracks only;
   - whether to add `tracks.drop_onground` (a config key and a TrackParams field together).
6. **Wind tolerance:** `cascade.wind_sys_mps` 15 m/s is much larger than the physics needs (about v·w/c ≈
   2 m/s; CLAUDE.md Known pitfalls). Tightening it is open, and it stays PROVISIONAL until 4.5.
7. **Session-3 defaults never formally approved** (details in docs/history.md):
   - `max_unmatched_events: 1` with lexicographic ranking;
   - probabilistic silence;
   - `gof_widening`;
   - the v1 ambiguity thresholds;
   - `audible_slant_m.unknown` = 3000 m;
   - `silence_explain: cpa`.

   CLAUDE.md steps 5–6 still describe the original (V0) design; update them only after the V0/V1 comparison
   settles it.
8. **Process:** CONTRIBUTING §1 says squash-merge. #9–#15 were squash-merged, but #16–#20 were merged with
   merge commits. Either the rule or the practice should change.

## Next work (candidates; each needs the owner's OK)
In the order the docs give:
- T3 (build fixtures once) and T4 (calibration checks only on change)
  ([reports/test_plan.md](reports/test_plan.md)).
- The accepted ADRs' action items:
  - 0002 ruff in the gate and 0001 the run environment in run.json (small);
  - 0004: log the TrackSet's K, T and bytes in run.json;
  - 0005 `run_locate` stage split (T2's golden rows now check it in CI).
- The V1k decision run (#26): from clean main on the compute machine, smoke run first.
- ADR-0006 (lockstep centre scoring), if accepted.
- Arms once they have a registered rule: V1s / V1r (#23). Also the targeted methodology review.
- Real data: field recordings (4.5), then P4.3 real-traffic replay and P5.3 UAT coverage.

## Machines and sessions
- **The compute machine** runs decision runs, ablations and screens, from a clean `main` only
  (CONTRIBUTING §4). A run there must not share the machine with test suites: timeouts are wall-clock.
- **The laptop** runs analysis, docs, reviews and QGIS. Its worktrees often lack `data/proj`, so the geoid
  test skips and scores can differ from the compute machine's in the 7th digit. Compare rows only from the
  same machine.
- Sessions coordinate through PRs and this file. Before editing a shared file (CONTRIBUTING §5), check the
  open PRs' file lists (`gh pr list --json number,files`).

## How to run
```bash
.venv/bin/python cli.py synth --clean --seed 0 --event-times 150,420 --duration-s 600 --cascade-decoys 3
.venv/bin/python cli.py locate --scenario outputs/scenarios/synth_seed0      # -> outputs/<run_id>/, outputs/latest
.venv/bin/python cli.py locate --scenario outputs/scenarios/synth_seed0 --variant V0   # a named config overlay
.venv/bin/python cli.py ablate --plan stageA --variants V0,V1 --jobs 8 --out outputs/ablation/<name>   # smoke run first (CONTRIBUTING §4.1)
.venv/bin/python cli.py ablate --summarize --out outputs/ablation/<name>
.venv/bin/python cli.py clean --keep 5
.venv/bin/python -m pytest -m "not slow and not heavy" -n 4   # the gate: about 1 min locally, about 30 s on CI
.venv/bin/python -m pytest -m "heavy or slow" -n 4            # after-merge suite: about 11 min locally, about 14 min on CI
QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python -s qgis/build_project.py [--verify]
```
- QGIS's Python must run with `-s`: user site-packages break its shapely import.
- Setup on a new machine (gitignored files):
  - `.venv` from requirements.txt;
  - the geoid grids in `data/proj` (README Quick start);
  - `outputs/plotly.min.js` (README outputs table);
  - `data/doppler.txt`, the third-party transcript the session-4 reports cite. It is not committed; copy it
    from another checkout.

## Fragile / watch
- **Timeouts are outcomes.** The 900 s per-run cap means scoring speed changes results on heavy units
  (CONTEXT.md). Identical-result speed-ups need the oracle check; result-changing ones are arms.
- **Dense TrackSet:** fine for the 1-min benchmark (about 0.3 GB, estimated), not for long US-wide clips
  (ADR-0004, accepted).
- **Hypothesis enumeration and the silence explanation lookup** loop in Python over survivors. This is fine
  regionally; profile before US-wide.
- **Per-event fit is straight-line.** On curved tracks σ is optimistic; `timing_sys_s` and `w_cov` cover it.
- **Code-review findings not fixed** (from the session 9 review; details in #23's description):
  - the ambiguity v1 tie test breaks for negative scores (calibrated likelihood with v1 only);
  - the silence-penalty denominator ignores unmatched events;
  - `UNMATCHED_COST = 9.0` and the 3·k missing-point penalty are hard-coded;
  - ridge decimation is quadratic in points.
- **pandas 4 deprecation warnings** come from inside fastparquet, and are harmless while pandas is pinned to
  3.1 (ADR-0003).
- **US-wide outputs:**
  - above `outputs.max_raster_cells`, score.tif is a sparse tiled GeoTIFF, not a COG;
  - the grid count is exact up to 5000 tiles, area-based beyond;
  - the template's `grid` layer is missing for US-wide runs.

## Where to find what
| Need | Document |
|---|---|
| Spec: physics, methodology, conventions, pitfalls | [CLAUDE.md](CLAUDE.md) |
| The plan and every recorded decision | [reports/combined_plan.md](reports/combined_plan.md) (§4 decisions, §7 answers, §8 current order) |
| Branches, reviews, smoke runs, CI policy | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Every knob's source and status | [reports/apriori_registry.md](reports/apriori_registry.md) |
| Baselines: Stage A, U18, Stage A2 | [reports/stageA.md](reports/stageA.md), rows in `reports/data/` |
| Detection work (U19) | [reports/u19.md](reports/u19.md), [docs/u19_detection_literature.md](docs/u19_detection_literature.md) |
| Performance history and its rules | [CONTEXT.md](CONTEXT.md) |
| Code health, ADRs, test strategy | [reports/tech_debt_audit.md](reports/tech_debt_audit.md), [docs/adr/](docs/adr/README.md), [reports/test_plan.md](reports/test_plan.md) |
| US-wide benchmark design (milestone 7) | [docs/design_uswide_benchmark.md](docs/design_uswide_benchmark.md) |
| Real-data preparation | [docs/field_recordings.md](docs/field_recordings.md), [docs/opensky_ingest.md](docs/opensky_ingest.md), [docs/adsblol_ingest.md](docs/adsblol_ingest.md), [docs/uat_coverage.md](docs/uat_coverage.md), [docs/pyopensky_dependency.md](docs/pyopensky_dependency.md) |
| Diagnostics | [docs/null_tests.md](docs/null_tests.md), [docs/osm_viewer.md](docs/osm_viewer.md) |
| Earlier sessions | [docs/history.md](docs/history.md) |
| Prompt for the next compute-machine session | [NEXT_SESSION.md](NEXT_SESSION.md) |
