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

Step 2, Phase 1 starting with P1.1, as a comparison arm, never a new default. Its acceptance test is U18:
on all-on C, n = 100 paired seeds (0-99; add an ablation plan for seeds 50-99 so Stage A's 0-49 are reused),
precision of unique must beat the 44 % status-ignored baseline without lowering rank-1 accuracy, and the
answer rate is reported. Add any new knob to reports/apriori_registry.md before its first measurement.

Step 3, §8.2 detection item (U19), alongside Phase 1, measured on recall and false events. Don't touch
detect.trigger_db or any other provisional threshold.

Rules: keep V0 and V1 runnable and compared; never tune provisional values on synthetic data; don't edit
code in a checkout while an ablation is running from it (use a separate worktree); run decision ablations
on a clean, committed tree; commit or push only when I ask. Stop and ask before any change to a CLAUDE.md
methodology step beyond U18/U19.
```
