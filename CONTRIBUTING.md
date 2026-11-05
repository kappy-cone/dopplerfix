# Working conventions: several Claude Code sessions, several machines

One person runs several Claude Code sessions, on different machines, sometimes at the same time. These
conventions keep that safe. They add to CLAUDE.md (methodology, rules) and the plan
(`reports/combined_plan.md`); they never override them.

## 1. Branches and merges
- **One branch per session or task.** Name it `s<session>-<topic>`, e.g. `s6-measurement`, `s6-p11-likelihood`.
  Start from an up-to-date `main` (`git pull --rebase`).
- **`main` changes only through a pull request** whose head commit passed the CI gate (§7) and that has been
  reviewed (§3). Squash-merge, so `main` keeps one commit per reviewed step.
- **Rebase the branch on `main` before opening or updating a PR.** Keep branches short-lived: merge within
  a day or two.
- **The user is the integrator.** A session opens PRs. It merges only when the user says so.

## 2. Commits
- **One commit per logical step, not per edit.** Each commit passes the gate tests locally
  (`pytest -m "not slow and not heavy" -n 4`, about 30 s). Run the heavy and slow tests
  (`pytest -m "heavy or slow" -n 4`) before asking for a merge when the change touches what they cover.
- **Commit messages say:**
  - what changed;
  - why (the plan item, e.g. "plan §8.1", "P1.1");
  - what is not yet verified.
- **Push the branch at each step,** so the work survives a crashed session or a dead machine and another
  session can review it.
- **Never commit credentials or local config** (OpenSky settings, `.env`, tokens). Check `git status` and
  the diff before each push.
- **No personal or machine details in any committed file or commit message** (the repo is public). This is
  a standing writing rule in CLAUDE.md Conventions, not a separate step before each push.

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

## 7. CI policy
CI is built for fast merges: a session pushes, and its checks are back in about 30 s.
- **The gate** (job `gate` in `.github/workflows/tests.yml`):
  - **When:** runs on every push to every branch. A PR therefore opens with its head commit already checked;
    opening it does not start another run.
  - **What:** the fast suite without the `heavy` tests (`pytest -m "not slow and not heavy"`). It is split into
    4 parallel jobs `gate (0/4)` … `gate (3/4)` by a hash of each test id (`--shard I/N` in `tests/conftest.py`),
    so every test runs in exactly one job and new tests need no bookkeeping. Each job runs 4 xdist workers, on
    a pinned runner image and Python 3.12, with the virtualenv cached on `requirements.txt`.
  - **Target:** about 30 s from push to result.
  - **Superseded runs:** a newer push to the same branch cancels the older run.
  - **Docs-only pushes** (`*.md`, `reports/`, `docs/`, `doppler.txt`) skip CI.
- **After merge** (job `after-merge`):
  - **When:** runs on every push to `main`, and by hand on any branch (Actions tab → tests → Run workflow,
    or `gh workflow run tests.yml --ref <branch>`).
  - **What:** the `heavy` tests (the locked-scoring true-cell checks) plus the slow suite, on 4 workers.
  - **Duration:** about 5–10 min. Nobody waits on it.
- **What blocks a merge:**
  - **A red gate on the PR's head commit.** A failure is fixed on the branch, or shown to be a false alarm
    (runner or network trouble, not the code) and re-run.
  - **A red after-merge run on `main`.** It blocks the *next* merge until it is fixed or reverted. Fix-forward
    is fine when it is quick; otherwise revert the merge. Whoever merged last owns the fix.
- **What CI does not check:** decision runs and ablations, QGIS (the template test only reads the project
  file), real data, other Python versions, and the rules a reviewer checks by hand (§3).
- **Keeping the gate at 30 s:**
  - A fast test that takes more than about 2 s on the runner gets either made faster or marked
    `@pytest.mark.heavy`, which moves it to the after-merge run. Find candidates in the gate log's
    `--durations` table.
  - Marking a test `heavy` is a reviewed change with its reason. A test is never marked `slow`, `heavy` or
    `xfail` just to turn a red gate green.
- **Geoid grids:** a missing-grid warning is not a failure (the test skips), but say so in the PR. If the
  warning appears although the cache was hit, the cached folder is bad. Bump the cache key (`proj-grids-v1` →
  `v2`, in `.github/actions/setup-env/action.yml`) so the grids are fetched again. A changed
  `requirements.txt` rebuilds the cached virtualenv once (about 30 s extra on that run).
- **Branch protection stays off** until CI has run on about 10 real PRs without a false alarm. Until then a
  red run is read by a person, and the user decides whether it blocks. Record false alarms in the PR they
  happened on. When protection is turned on:
  - make the four `gate (i/4)` checks required;
  - docs-only commits skip the workflow, so either drop `paths-ignore` then, or add no-op jobs with the same
    names for docs-only changes.
