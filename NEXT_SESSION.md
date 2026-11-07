# Prompt for the next compute-machine session

**No prompt is queued.** The owner writes the next one after choosing among the candidates in HANDOFF.md
("Next work").

- **Last brief:** `reports/test_plan.md` §7, T1 and T2. Done (#21, #22, merged).
- **Brief before that (session 8, task B, the Stage A2 reference run):** done in #16. Its text is in git
  history (`git show b33ebaa:NEXT_SESSION.md`).

## Template
Every prompt so far followed this shape; keep it, so a session can't miss a rule.

```
Pull first (git pull --rebase on main), then read CLAUDE.md (incl. the personal-details rule and the smoke-run
rule under Validation), CONTRIBUTING.md §4, §4.1 and §7, HANDOFF.md, and <the reports this task builds on>.

What the task is
- <one paragraph: what, why, what it is for, and what it is not (e.g. not a decision run)>

Prerequisites on main (check first; stop and tell me if one is missing)
- <merged PRs or config values the task needs, each with a one-line check>

Steps
1. <smoke run, if the job takes over ~15 min (CONTRIBUTING §4.1)>
2. <the work, each step with its output path>
3. <cross-check or acceptance rule, fixed in advance>
4. <where results are committed and reported>
5. Branch <name>, a PR, then STOP for me.

Rules
- One branch per task, commits with the gate green, a PR. Don't push to main without asking.
- Decision and reference runs only from clean main. Never edit code in a checkout while a run uses it.
- Keep V0 and V1 runnable and compared. Never move a provisional value. Never tune on synthetic data.

Stop point: <when to stop and wait>.
```
