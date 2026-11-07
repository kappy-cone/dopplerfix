# Two-stage refinement (arm V1k, K = 50): decision run (session 9)

**Verdict: REJECT.** D1 and D2 pass (accuracy unchanged on every unit). D3 fails: the median scoring speed-up
is 1.57×, below the registered 2×. `score.refine_lbfgs_top_k` stays null (off); V1k stays a registered,
rejected arm.

Registered in `reports/apriori_registry.md` ("Two-stage refinement") with the rule and
`reports/experiments/two_stage_decision.py` before the run. K = 50 and the rule were approved by the user.

## Run
- `cli.py ablate --plan stageA --variants V1k --jobs 8 --out outputs/ablation/two_stage` from clean main at
  15a7e55 (#26). Same compute machine as Stage A2, nothing else running.
- 275 units, 275 rows, about 44 min wall time. Rows: `reports/data/two_stage_rows.jsonl.gz`.
- **Smoke run** (CONTRIBUTING §4.1, `--limit 3` into a scratch folder):
  - the arm is applied: the variant config gives `ScoreParams.refine_lbfgs_top_k` = 50 for V1k (None for V1), and that is the config `run_locate` builds its scoring parameters from (`cli.py:877`);
  - the 3 truth.json files equal Stage A2's;
  - rows complete (40 fields, as A2), commit 15a7e55 clean, fresh output folder. Outcomes not read.
  - Note: `config_sha256` in rows hashes the whole config.yaml, so V1 and V1k rows from one commit carry
    the same hash. It cannot show which arm ran.

## Result (paired by unit against the Stage A2 V1 rows)

| criterion | A2 V1 | V1k | bar | result |
|---|---|---|---|---|
| D1 rank-1 accuracy | 107 / 275 | 107 / 275 | ≥ 107 | pass; 0 gained / 0 lost |
| D2 confident-wrong | 17 | 17 | ≤ 17 | pass |
| D3 median A2 / V1k score_s, units with > 50 candidates (n 249) | | 1.57 (IQR 1.20–1.90) | ≥ 2 | **fail** |

| reported | A2 V1 | V1k |
|---|---|---|
| correct | 62 | 62 |
| timeouts | 3 | 3 (the same units' statuses unchanged) |
| recall | 455 / 550 | 455 / 550 |
| total score_s | 13,945 | 11,617 (−17 %) |
| total row wall s | 17,332 | 14,984 |

- **No outcome or status changed on any unit**, and rank-1 error differs on 1 unit only (Wilcoxon p = 0.32).
- The 26 units with ≤ 50 candidates, where the arm cannot act, have identical rank-1 scores, as built.
- True-cell rank changed on 62 units (49 better, 13 worse; Wilcoxon p = 0.0002). It never changed at rank
  1: the smallest A2 rank among them is 10. Cells outside the best 50 keep their start-grid score.

**Speed by unit size** (median per-unit ratio): 50–200 candidates 1.41 (n 18), 200–500 1.82 (n 54),
> 500 (refinement capped at 500 cells) 1.55 (n 177).

## Reading
- K = 50 is safe for accuracy on this plan: the top of every ranking is untouched.
- The speed-up is small because, after #15, L-BFGS-B refinement is no longer most of the scoring time.
  - Removing about 90 % of the refinement (450 of 500 capped cells) saves 17 % of total scoring.
  - On the capped units that puts refinement at roughly 40 % of their scoring time. The rest is centre
    scoring of every hypothesis.
- That is what CONTEXT.md and ADR-0006 describe: centre scoring is the bottleneck, and two-stage refinement
  doesn't touch it. The earlier 6–7× estimate assumed refinement dominated, and it doesn't.
- Timeouts are unchanged (3 → 3), so the arm doesn't help the units that hit the cap either.
- **Not acceptance, and not to be re-run with a different K after seeing this.** A smaller K only cuts the
  remaining refinement share, so it can't reach 2× here. A new K would be a new registration.
