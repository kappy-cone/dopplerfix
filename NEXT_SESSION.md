# Prompt to continue the PC session (session 5, after Stage A)

Paste the block below into the running session on the PC, after `git pull --rebase`.

```
Pull first (git pull --rebase), then read reports/combined_plan.md §8 (the amendment after Stage A)
and reports/stageA.md. §8 sets the order for this session; accuracy and functionality outrank cost.

Decisions: U18 and U19 in §8.5 are approved (already recorded in the plan).

Step 1, §8.1 measurement fixes: reporting only; re-summarise the existing Stage A rows, no re-run.
Add to doppler/evaluate.py and the ablate summary, with tests:
- detection recall per config;
- a `detection` failure class reported beside the pre-registered taxonomy (don't change the rule);
- conditional accuracy (runs where every truth event was detected);
- rank-1 accuracy ignoring status;
- answer rate;
- the paired continuous tests from plan §3.0 (rank-1 error, true-assignment rank, Δcost).
Hand-check at least 5 Stage A rows against their kept truth and events before trusting the new metrics.
Write the results into reports/stageA.md as an addendum.

Step 1b (before Phase 1): profile score.rank_cells. Read-only first: cProfile the slowest Stage A V1
units, the 900 s timeouts (all-on seeds 11 and 23) and the 69,348-hypothesis clip, rebuilt from their
seeds. Report where the time goes. Then:
- an identical-result speed-up may go in only if it reproduces the Stage A rows exactly on at least 5 seeds,
  for both V0 and V1, and lands as its own commit before any P1.1 change;
- anything that changes results (top-K hypotheses per cell, a lower cap) stays a measured arm for me to decide.

Step 2, Phase 1 starting with P1.1, as a comparison arm, never a new default. Its acceptance test is U18:
on all-on C, n = 100 paired seeds (0-99; add an ablation plan for seeds 50-99 so Stage A's 0-49 are reused),
precision of unique must beat the 44 % status-ignored baseline without lowering rank-1 accuracy, and the
answer rate is reported. Add any new knob to reports/apriori_registry.md before its first measurement.

Step 3, §8.2 detection item (U19), alongside Phase 1, measured on recall and false events. Don't touch
detect.trigger_db or any other provisional threshold.

Guards: a speed-up counts as identical only if V0/V1 rows match Stage A bit-for-bit; otherwise it is
a result-changing arm for me to decide. Detection changes go behind a config flag, off by default in
V0 and V1; the U18 P1.1 run uses detection off, and detection is measured in its own comparison. Commit
after each step, and stop for my go-ahead after Step 2 before starting Step 3.

Rules: keep V0 and V1 runnable and compared; never tune provisional values on synthetic data; don't edit
code in a checkout while an ablation is running from it (use a separate worktree); run decision ablations
on a clean, committed tree; commit or push only when I ask. Stop and ask before any change to a CLAUDE.md
methodology step beyond U18/U19.
```
