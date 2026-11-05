# Working conventions: several Claude Code sessions, several machines

One person runs several Claude Code sessions, on different machines, sometimes at the same time. These
conventions keep that safe. They add to CLAUDE.md (methodology, rules) and the plan
(`reports/combined_plan.md`); they never override them.

## 1. Branches and merges
- **One branch per session or task.** Name it `s<session>-<topic>`, e.g. `s6-measurement`, `s6-p11-likelihood`.
  Start from an up-to-date `main` (`git pull --rebase`).
- **`main` changes only through a pull request** that passes CI (`.github/workflows/tests.yml`, fast tests)
  and has been reviewed (§3). Squash-merge, so `main` keeps one commit per reviewed step.
- **Rebase the branch on `main` before opening or updating a PR.** Keep branches short-lived: merge within
  a day or two.
- **The user is the integrator.** A session opens PRs. It merges only when the user says so.

## 2. Commits
- **One commit per logical step, not per edit.** Each commit passes the fast tests
  (`pytest -m "not slow"`). Run the slow tests before asking for a merge.
- **Commit messages say:**
  - what changed;
  - why (the plan item, e.g. "plan §8.1", "P1.1");
  - what is not yet verified.
- **Push the branch at each step,** so the work survives a crashed session or a dead machine and another
  session can review it.
- **Never commit credentials or local config** (OpenSky settings, `.env`, tokens). Check `git status` and
  the diff before each push.

## 3. Review
- **Review at the plan's checkpoints, not on every push.** For example, after each numbered step of a session
  prompt. The author keeps working while the review runs; it does not wait.
- **The reviewer is a separate session.** It gets:
  - the diff (`git diff main...<branch>`);
  - the plan section the change implements.

  It does not get the author's conclusions, so it is not anchored on them.
- **The reviewer is read-only.** It writes findings as PR comments or as `reviews/<branch>.md`, and never
  fixes code itself. The author addresses each finding (fix, or rebut with evidence).
- **A reviewer always checks the rules that break quietly:**
  - no provisional value moved to hit a target (`reports/apriori_registry.md` rule; CLAUDE.md);
  - V0/V1 code paths unchanged, or the change says so. If they changed, the Stage A reference rows no
    longer apply and the reference must be re-run;
  - new thresholds in config.yaml with a source in the registry;
  - pure functions in `doppler/*`, file I/O only in `cli.py` / `export.py` / `plot.py`;
  - tests for every change;
  - nothing that feeds diagnostics back into a run (CLAUDE.md Operating modes).

## 4. Experiments and results
- **Decision runs** (the ones a plan decision rests on, e.g. Stage A, the U18 acceptance run) run **only from
  a clean, committed `main`** (or a tag). Their rows record the commit. Ablations from a feature branch are
  screens only.
- **Never edit code in a checkout while an ablation runs from it.** Each scenario's worker imports the code
  from disk. Use a separate worktree (`git worktree add ../qgis-dev -b <branch>`).
- **Never mix ablation rows from different machines** in one `rows.jsonl`. Timeouts are wall-clock and
  machine-dependent.
- **Make decision results reviewable.** Commit a gzip-compressed copy of a decision run's `rows.jsonl` next to
  its report (e.g. `reports/data/stageA_rows.jsonl.gz`), so a reviewer on another machine can recompute the
  numbers. Screens and other runs stay in `outputs/` (gitignored).

## 5. Ownership: avoid two sessions in the same files
- **Each session prompt states its scope** (files or modules it may change). Two sessions never take the
  same module at the same time.
- **Shared files are conflict hot spots.** Edit them in small, separate commits, and only on the session's own
  branch:
  - `HANDOFF.md`
  - `reports/combined_plan.md`
  - `config.yaml`
  - `cli.py`
  - `NEXT_SESSION.md`

  Planned: per-session notes files under `handoff/` with a short index, and splitting `cli.py` into a package.
- **Binary files cannot be merged** (`qgis/doppler_template.qgz`). Only one session touches them at a time.
- **Plan decisions belong to the user.** A session records a decision in the plan only when the user has made
  it, and never reinterprets one recorded by another session.

## 6. Machine roles
- **The compute machine** runs ablations and other compute-heavy work.
- **The laptop** handles analysis, docs, review sessions and QGIS.
- Either can write code, on its own branch.
