# ADR-0006: Centre scoring in lockstep per cell, grouped by (event, track), with identical results

**Status:** Accepted (2026-11-06)
**Date:** 2026-11-06
**Deciders:** project owner
**Audit item:** 4 (CONTEXT.md, "Options already identified but not done")

## Context
On the units that hit the 900 s ablation cap, centre scoring of every hypothesis takes 88–91 % of the run
(session 5 profile, reports/stageA.md §1b). #15 sped up in-cell refinement but left centre scoring unchanged
(1.16–1.29× on hypothesis-heavy units). Timeouts are outcomes (CONTEXT.md), so this is the bottleneck that
decides results on heavy units.

Today, `score_hypothesis` runs for each hypothesis, one after another:
1. a coarse Δt grid: one `_hyp_cost` call over G values of Δt;
2. a bounded Brent search (`scipy.optimize.minimize_scalar(method="bounded")`): about 18 `_hyp_cost` calls,
   one Δt each;
3. a final `_hyp_cost` at the best Δt.

Each `_hyp_cost` call loops over events and calls `modeled_ratio(xyz, track, ts, t_rx)` per matched event, on
arrays of about 60 points. The profile shows per-call overhead, not arithmetic.

**Structure to exploit.** The cost of a hypothesis is a sum over events, and **each event's term depends only
on (cell position, that event's track, Δt)**. A cell's hypotheses are combinations of a few candidate tracks
per event (cascade.py's best-first enumeration), so many hypotheses share the same (event, track) pairs.
`_hyp_cost` already evaluates one track at many Δt in one call, and #15 relies on (and tests) the guarantee
that column g of a multi-Δt call is bit-identical to a single-Δt call.

## Decision
Score each cell's hypotheses **in lockstep**:
- **Brent as a coroutine.** Port scipy 1.18.1's `_minimize_scalar_bounded` to a Python generator that yields
  each probe x and receives f(x). It is the same scalar float arithmetic, line for line, so the probe sequence
  and the result are identical to scipy's.
- **One step for all hypotheses.** Advance every active hypothesis' generator by one step. Collect the
  requested Δt values, **group them by (event, track)**, and make **one** `modeled_ratio` / `_event_cost` call
  per group with all of that group's Δt values as columns. Each hypothesis' f(x) is the sum of its events'
  columns, in the same event order as `_hyp_cost` sums them.
- **Batch the other two phases the same way.** The coarse grids (each hypothesis' own grid, concatenated per
  group) and the final evaluation.
- **Keep the old path** (`score_hypothesis`) as the oracle in `tests/_scoring_oracle.py`. The new path must
  match it field for field.

Expected effect: calls per Brent step fall from (hypotheses × events) to (distinct tracks per event × events).
This is an estimate, to be confirmed by re-profiling the four session-5 units (all-on 11, 23, 18 and
loo:source 6) before and after.

## Options considered

### A. Lockstep generator-Brent with (event, track) grouping (chosen)
| Dimension | Assessment |
|---|---|
| Complexity | Medium: a 150-line Brent port plus a grouping driver, about 300 lines in all |
| Results | Identical by construction, and checked against the oracle and rebuilt Stage A2 units; only timeout statuses can change |
| Speed | Large where hypotheses per cell are many and tracks per event few, which is exactly the heavy units |
| Dependencies | None |

### B. numba-compiled `modeled_ratio` / `_event_cost`
| Dimension | Assessment |
|---|---|
| Complexity | Medium |
| Results | **Not guaranteed identical**: reduction order and FMA contraction can differ from numpy, so the 7-digit row cross-checks would fail |
| Dependencies | numba + llvmlite (about 150 MB). CLAUDE.md asks to flag the hot spot first, which is done, but A should come first |

### C. Result-changing arms: two-stage refinement, top-K hypotheses per cell, a lower cap
Already planned as registered arms (NEXT_SESSION). They are complementary, not alternatives. A makes every arm
cheaper to evaluate, and it doesn't need a registry entry.

### D. Multiprocessing within a run
No throughput gain, because ablations already run `--jobs 8` on all cores. It would also make the
per-run wall clock, and so timeouts, depend on pool scheduling.

### E. Raise or remove the 900 s cap
Trivial, but it spends compute rather than saving it, and the cap protects the run budget.

## Trade-off analysis
A is the only option that is both faster and identical. Its risk is a porting mistake in Brent or in the
summation order, and that is exactly what the oracle test catches. Bit-identity across Δt columns is already
relied on and tested (#15). The work stays inside `score.py`, so it doesn't collide with ADR-0005 (stage
split, `cli.py`) or ADR-0004 (no TrackSet layout change).

## Consequences
- Easier: ablations and screens finish sooner; fewer timeouts; the two-stage refinement arm becomes cheaper
  to decide.
- Harder: `score.py` gains a driver whose control flow is less obvious than a loop over hypotheses. The
  docstring and the oracle test document it.
- Revisit: if scipy is bumped, re-port the Brent loop or confirm it is unchanged (a test compares the port's
  probe sequence with scipy's on random problems).
- Results: timeout statuses will change on heavy units, so rows differ there (an existing, documented
  exception in cross-checks). Rerun a reference only if you want timeouts on the new code.

## Action items
1. [ ] Re-profile the four session-5 units on current main: the baseline, plus hypotheses per cell and
   distinct tracks per event.
2. [ ] `score._bounded_brent` generator; test that its probes equal scipy's on 200 random bounded problems.
3. [ ] `score.score_cell_lockstep` (coarse grid → Brent steps → final), used by `rank_cells` behind no flag
   (identical results).
4. [ ] Oracle test: every `CellScore` field identical to `score_hypothesis` on the cascade survivors of a
   clean scenario and of one all-on unit; 10 rebuilt Stage A2 units identical except timing and provenance.
5. [ ] Re-profile; report the speed-up per unit in the PR.
