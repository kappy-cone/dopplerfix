# Prompt for the next session (session 8, U19 task 3: the arm-b decision run, then task 4)

Paste the block below as the first message of the session, run from the repo root on the compute machine.

```
Pull first (git pull --rebase on main), then read, in order:
- CLAUDE.md (incl. the personal-details rule in Conventions) and CONTRIBUTING.md (§1 branches/PRs, §4 decision
  runs, §7 CI);
- HANDOFF.md START HERE (session 8: commands, time estimate, what to check first);
- reports/u19.md: the D1 + E1 section and "Step 3: pre-registered acceptance rule for arm b" (approved,
  unchanged; C3 stays a strict point rule).

State on main (PR #6 merged):
- config.yaml variants V1b / V0b = V1 / V0 + detect.long_window_s 3.0; V0 and V1 unchanged.
- The decision analysis is fixed in reports/experiments/u19/decision.py. On the reference rows renamed as an
  arm, it reproduces the U18 table exactly.
- D1: "window, no fit" misses are lost at the Hough seed -> guided ridge hand-off (the peel breaks out of the
  window on a short guided ridge), not at the 12 dB gate. E1 all-on recall: base 75, b 82, oracle windows 84,
  oracle seed 87, truth ridge 98.

Tasks, in order:
1. (U19 task 3) The decision run from clean main on this machine, --jobs 8, 900 s cap: V0b and V1b, all-on C
   seeds 0-99 (commands in HANDOFF). Expect about 35-50 min for the run and about 1-1.25 h in total. Tell me
   before starting if anything differs from HANDOFF.
   - Run decision.py. Read the validity checks first; if either fails, stop and investigate.
   - Hand-check at least 5 seeds whose V1 rank-1 result changed.
   - Commit the rows as reports/data/u19_rows.jsonl.gz, and the results and verdict in reports/u19.md.
     Work on a branch (s8-u19-decision) in a separate worktree while the run uses the main checkout; open a PR.
   - STOP for me. Making detect.long_window_s 3.0 the default is my decision.
2. (U19 task 4, only after my OK) The seed-to-ridge hand-off arm (approved in session 7):
   - registered in reports/apriori_registry.md before its first measurement;
   - off by default;
   - first measure the seed curve's offset from the ridge;
   - screened on the six configs, seeds 0-49, base and arm, paired by audio (extend d1.py).

Rules:
- One branch per task, commits with the gate green, a PR. Don't push to main without asking.
- Decision runs only from clean main. Never edit code in a checkout while a run uses it (use a worktree).
  Screens only rule ideas out; never re-run just the seeds that failed.
- Keep V0 and V1 runnable and compared. Never move detect.trigger_db, ridge.weak_db or any provisional value.
  Never tune on synthetic data. Don't change the pre-registered rule after seeing data.
- Stop and ask before any change to a CLAUDE.md methodology step beyond U18/U19.
- Config items that wait for me until after U19's decision run: aircraft_types.type_table_path, and
  tracks.drop_onground.

Stop points: after task 1 (decision run), after task 2 (hand-off screen).
```
