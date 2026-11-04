# Phase 0 pilot (P0.6), session 4

**Run.** `cli.py ablate --plan pilot`: 5 × scenario C (60 s, events at 15/45 s) and 1 × scenario A (600 s,
150/420 s), all realism on, 4 arms. Raw rows are in `outputs/ablation/pilot/rows.jsonl` (gitignored, 1.4 MB;
kept on the Mac only).
- V0 and V1: the Phase 0 code, with the P0.1 fixes.
- V0-asdesigned and V1-asdesigned: tag `variants-asdesigned`, on the same scenarios.

The working tree was uncommitted (`--allow-dirty`). This is a pilot, not a baseline.

## Outcomes (n = 6 per arm; indicative only)

| arm | correct | abstained | wrong | confident-wrong | timeout |
|---|---|---|---|---|---|
| V0 (strict) | 0 | 6 | 0 | 0 | 0 |
| V1 (robust) | 1 | 4 | 0 | 0 | 1 |
| V0 → V1 fallback (either trigger) | 1 | 4 | 0 | 0 | 1 |
| V0-asdesigned | 0 | 5 | 0 | 1 | 0 |
| V1-asdesigned | 1 | 2 | 0 | 2 | 1 |

- **The absolute-fit status is doing its job.** Three wrong answers (2.3–6.6 km away) were labelled `unique`
  by the as-designed code: C2 under both variants, and A0 under V1. That counts as confident-wrong. The new
  code flags each of them `inconsistent`, so they count as abstentions. No correct answer was flagged.
- **The true assignment is mostly lost at CPA.** Failure taxonomy, Phase 0 arms:
  - V1: 3 survival:grid (never passed CPA), 1 bias (C1: right aircraft pair at rank 1, 647 m off), 1 timeout.
  - V0: also C3, where the true pair died at the offset stage.

  The pre-registered rule (plan §3.0) therefore says: **P3.5 (tolerance restructure) before Phase 2.** This
  agrees with the skeptics' finding that fit uncertainties are over-confident under realism (S-F2/F10). It
  rests on 5 seeds, so it needs Stage A before it decides anything.
- **C0 is changed by the tile-pruning fix (P0.1b).** As designed, V1 gave zero survivors there (5 detected
  events, 3 of them spurious). The fixed V1 now scores 985 cells and abstains, `inconsistent`.
- **Mostly identical where comparable.** C1 and C3 give the same rank-1 answer in both code versions.

## Compute (C scenarios, seconds per arm)

| arm | per scenario | mean |
|---|---|---|
| V0 | 10, 0, 8, 51, 81 | 30 |
| V1 | 55, 77, 323, 510, 900 (timeout) | 373 |
| V0-asdesigned | 24, 88, 9, 20, 183 | 65 |
| V1-asdesigned | 69, 75, 86, 417, 900 (timeout) | 309 |

Synthesis plus detection add about 30–60 s per scenario.
- **Scoring dominates V1.** Clips with spurious events produce up to about 16,000 hypotheses.
- **The P0.1-i target ("< 60 s replays") is not met.** The best-first caps keep the run time bounded but
  not small.
- **Stage A estimate.** 275 C scenarios × (V0 + V1) at about 7.5 min each, plus the as-designed arms on
  20 seeds, comes to about 36 h of compute, about 12 h wall at `--jobs 3`. That is roughly 3× the plan's
  13 h.

Options before Stage A, each needing a decision:
1. Run as is, on a machine with more cores (`--jobs` scales roughly linearly while RAM allows).
2. Lower the per-run cap to 600 s, so timeouts become a reported failure class sooner.
3. Profile `score.rank_cells`: centre scoring of every hypothesis, then refining up to 500 cells. It is a
   hot spot CLAUDE.md asks to flag before reaching for numba or multiprocessing.

## Known gaps in the pilot

- The as-designed arms report only the old run.json fields: no failure class, assignment rank or per-event z.
- One timeout per robust arm (C4).
