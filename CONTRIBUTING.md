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
- **No personal or machine details in any committed file** (the repo is public). That means no hardware specs
  (CPU, cores, RAM, GPU, disk), hostnames, OS usernames, home-directory or temp paths, email addresses or names.
  Use neutral terms ("the compute machine", "the laptop") and placeholders (`<repo>`, `<scratch>`, relative
  paths). Before each push, scan the staged diff, e.g.
  `git diff --cached | grep -inE "/Users/|/home/|/private/tmp|@[a-z0-9.-]+\.(edu|com)|cores|threads|[0-9]+ ?GB"`,
  and check every hit. This applies to text a subagent or another session produced, too.

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
- **What CI checks** (`.github/workflows/tests.yml`):
  - the fast suite (`pytest -m "not slow"`) on every pull request and every push to `main`, on a pinned
    runner image and Python 3.12 with the pinned `requirements.txt`. It runs in three parallel shards (two
    for the locked-scoring tests, one for everything else); the target is under about 5 minutes
    wall-clock per run;
  - nothing on changes that touch only documentation (`*.md`, `reports/`, `docs/`, `doppler.txt`);
  - the slow suite (`pytest -m slow`) only when started by hand: Actions tab → tests → Run workflow, or
    `gh workflow run tests.yml --ref <branch>`. Run it there, or locally, before asking for a merge (§2).
- **What CI does not check:** decision runs and ablations, QGIS (the template test only reads the
  project file), real data, other Python versions, and the rules a reviewer checks by hand (§3).
- **What blocks a merge:** a red fast-suite run on the PR's latest commit. A failure is fixed on the branch,
  or shown to be a false alarm (runner or network trouble, not the code) and re-run. It is never skipped by
  marking a test slow or `xfail` just to get green: moving a test between suites is a reviewed change with
  its own reason. A missing geoid-grid warning is not a failure (the test skips), but say so in the PR.
  If the warning appears although the cache was hit, the cached folder is bad. Bump the cache key
  (`proj-grids-v1` → `v2`, in `.github/actions/setup-env/action.yml`) so the grids are fetched again.
- **Branch protection stays off** until CI has run on about 10 real PRs without a false alarm. Until then a
  red run is read by a person, and the user decides whether it blocks. Record false alarms in the PR they
  happened on. When protection is turned on, make the three `fast (…)` checks required; because docs-only PRs
  skip the workflow, either drop `paths-ignore` then or add a no-op job with the same check names.
- **Keep it cheap:** if the fast suite grows past about 5 minutes, find the slow tests in the run log's
  `--durations` table and fix the cause first; another shard or a move to the slow suite is a last resort.
