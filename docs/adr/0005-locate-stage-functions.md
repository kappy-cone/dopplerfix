# ADR-0005: Split `run_locate` into pure stage functions in `doppler/pipeline.py`

**Status:** Proposed (2026-11-06; awaiting the project owner's decision)
**Date:** 2026-11-06
**Deciders:** project owner (not yet decided)
**Audit item:** 3

## Context
`cli.run_locate` (`cli.py` about 700–1040) runs these steps in one function: audio → events, the time prior,
track loading and windowing, truth lookup, the tile-by-tile cascade, the interim ranking, locked scoring,
ambiguity and truth evaluation. Truth bookkeeping is interleaved with the cascade loop: `true_rows` and
`ta_stage` update per tile. `write_outputs` (about 200 lines) and the ablation harness (about 500 lines,
`ablation_plan` … `ablate_summary`) also live in `cli.py`.

Callers: `cmd_locate`, the ablation worker (`_ablate_worker`, with precomputed `fits`), `run_null_tests`
(through `LocateResult`), and `tests/test_locate.py`. The P0.3 seam (`LocateInputs` / `LocateResult`, and
`fits=` injection) already exists, so the outer contract is stable.

The pressure comes from CLAUDE.md's HITL design constraint: "keep stages separable with explicit inputs and
outputs … no hidden state between stages". Milestone 9 has to insert analyst decisions between stages: reject
an event, exclude a track or an area, reject a candidate. Today a stage is a span of lines inside one
function, not something a caller can stop after, inspect or feed.

## Decision
Extract the stages into `doppler/pipeline.py` as pure functions with typed results, keeping
`cli.run_locate` as a thin composer with the same signature and the same `LocateResult`:

```
events_stage(x, fs, cfg, c)                      -> EventsResult      (fits, events)
tracks_stage(cfg, tracks_df, registry, window)   -> TracksResult      (prepared, ts, prior)
cascade_stage(spec, events, ts, prior, dem, ...,
              observer=None)                     -> CascadeResult     (survivors, counts, stage_s, culprits)
score_stage(events, survivors, ts, ...)          -> ScoreResult       (ranked, ambiguity)
evaluate_stage(truth, events, cascade, score)    -> TruthEval | None
```

- Truth tracking leaves the cascade loop. It becomes an optional `observer` callback that `cascade_stage`
  calls per tile with the tile's result, so the stage has no truth-specific code and real runs pass none.
- File I/O stays in `cli.py` (CLAUDE.md: pure functions in `doppler/*`). `pipeline.py` takes arrays and
  DataFrames, not paths.
- Each extraction is its own PR, checked **bit-identical** on rebuilt Stage A2 units (about 10 units × V0/V1,
  every result field) plus the existing `test_locate` contract tests.
- Order: score → evaluate → cascade (with the observer) → tracks → events. The later stages first, since they
  have the simplest inputs.
- The ablation harness's pure parts (plan expansion, row building, paired tests) move to `doppler/ablation.py`
  afterwards. Process pools, disk guards and the as-designed subprocess stay in `cli.py`.

## Options considered

### A. Leave it until milestone 9
| Dimension | Assessment |
|---|---|
| Cost now | None |
| Cost later | The split happens under milestone-9 pressure, mixed with HITL features, which makes "same results" harder to prove |

### B. Stage functions in `pipeline.py`, incremental (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Medium: 5 PRs of about 100–200 lines moved each |
| Risk | Low per step, with the bit-identity check; the outer API (`LocateInputs` / `LocateResult`) is unchanged |
| Benefit | Each stage testable alone; HITL decisions slot in between stage calls later; ablation logic testable without the CLI |

### C. A stage-graph framework with persisted intermediates (cache, resume, decision files)
Rejected now. It is HITL tooling, which CLAUDE.md says not to build yet, and it conflicts with the storage rule
of not caching intermediates by default.

### D. Split `cli.py` into several CLI modules without changing `run_locate`
It moves lines, but leaves the stages inseparable. Cosmetic.

## Trade-off analysis
The cost is mechanical refactoring under a strict identity check. The benefit is the HITL constraint met
before it is needed, plus smaller units to test. Doing it now, while no result-changing work touches
`run_locate`, is cheaper than during milestone 9. It is independent of ADR-0006 (`score.py` internals): only
`score_stage`'s call into `score.rank_cells` crosses both.

## Consequences
- Easier: unit tests per stage; an analyst decision later becomes "filter the stage result, call the next
  stage"; experiment scripts can call a single stage.
- Harder: one more module; `LocateResult` assembly in `cli.py` becomes field plumbing from the stage results.
- Revisit: the stage boundaries, after the first HITL design (milestone 9).

## Action items
1. [ ] A frozen reference: rebuild 10 Stage A2 units (V0/V1) on the branch base; keep their rows as the check.
2. [ ] PR 1: `score_stage` + `evaluate_stage`. PR 2: `cascade_stage` with the observer. PR 3: `tracks_stage`.
   PR 4: `events_stage`. PR 5: `doppler/ablation.py`.
3. [ ] Each PR: the 10 units are identical on every result field; the gate is green.
