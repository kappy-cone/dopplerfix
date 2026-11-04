# A-priori registry of new knobs (reports/combined_plan.md P0.7)

Every knob added from Phase 0 onwards is listed here **before** the first measurement that uses it. Each
entry has a value, a source (physics / standard / literature / measured-in-data / arbitrary), a status, and
a fixed sensitivity grid. The values are also in config.yaml, with the same source in a comment.

**Rule.** A missed target is reported, never fixed by moving a provisional value. Moving a value needs a new
source (milestone 4.5 field data or physics), recorded here with the date. Reserved seeds (P4.1) add to
this rule; they don't replace it. CLAUDE.md applies too: provisional thresholds are never tuned against
synthetic data.

## Phase 0 (session 4, before the pilot and the Stage A baseline)

| Knob (config key) | Value | Source | Status | Sensitivity grid | Used by |
|---|---|---|---|---|---|
| `cascade.max_event_radius_m` | 12000 | physics: 2 × the largest audible slant (jet 6 km) | PROVISIONAL | 8000, 12000, 20000 | `cascade.build_event_index` |
| `cascade.weak_event_rel_sigma` | 0.5 in V1; null (off) in V0 | arbitrary | PROVISIONAL | 0.3, 0.5, 1.0 | `cascade.gating_events` (user F1: off in strict) |
| `cascade.max_partial_per_cell` | 16 | arbitrary | PROVISIONAL | 8, 16, 64 | `cascade.offset_filter` |
| `cascade.enum_budget_per_cell` | 5000 | arbitrary (algorithmic budget) | PROVISIONAL | 1000, 5000, 20000 | `cascade.offset_filter` |
| `score.inconsistent_rms_sigma` | 3.0 | statistics: 3σ convention | PROVISIONAL | 2, 3, 5 | `score.absolute_fit_status` (status only, never ranking) |
| `evaluate.success_radius_m` | 200 | user decision U10 (fixed before any result) | FIXED | — (report 150 alongside) | `evaluate.outcome` |
| `evaluate.confident_wrong_m` | 1000 | plan §3.0 metric definition | FIXED | — | `evaluate.outcome` |
| `evaluate.match_tol_s` | 10 | existing run.json truth matching (unchanged) | FIXED | — | `evaluate.match_events` |
| `evaluate.hop_ratio_tol` | 0.015 | arbitrary (diagnostic only) | PROVISIONAL | 0.01, 0.015, 0.03 | `evaluate.harmonic_hops` |

Unchanged existing caps used by the new enumeration: `score.max_hypotheses_per_cell` 64 (now the fully
matched cap; partial hypotheses have their own cap above).

## Later phases

These entries are listed in plan P0.7 and are added to config.yaml only when their item is built:
`score.outlier_frac`, `score.adsb_budget`, `ambiguity.tie_quantile`, `ambiguity.gof_quantile`,
`score.top_assignments`, `score.p_unmatched`, `tracks.max_bridge_s`, `tracks.baro_radius_km`,
`silence.source_level_db`, `silence.null_shifts_s`, `uncertainty.nuisance_sigma`.
