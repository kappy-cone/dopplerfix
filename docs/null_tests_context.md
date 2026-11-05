# Session context: time-shift null tests and a blind result commitment

Brief for the session that builds these two diagnostics, on branch `s7-null-tests`. Read `CLAUDE.md` and
`CONTRIBUTING.md` first; this note only adds what is specific to the task.

## 1. Where the ideas come from
[Espaye/aircraft-doppler](https://github.com/Espaye/aircraft-doppler) (MIT) is an independent tool with the
same goal as this repo. Its `docs/method.md` (§5–6) describes two checks this repo lacks:

- **Time-shift null tests.** All events are moved against the flight data by a quarter, half and three quarters
  of the recording, wrapped around, and searched again. If shifted data fit almost as well as the real data, the
  match may be coincidence.
- **Blind validation.** The result and its SHA-256 hash are written before any reference is consulted. A later
  `validate` step re-checks the hash before computing the error.

Reimplement the *ideas* from this description. Do not copy their code. The two repos currently share no code:
a line-level comparison found only 10 generic one-line idioms in common.

## 2. Why it matters here
**Null tests.** Under full realism, the robust variant V1 is confidently wrong in 11 of 100 runs, and V1c in
4 of 100 (U18 table in `reports/stageA.md`). The status labels (`unique`, `ambiguous`, `inconsistent`,
`locus`) don't separate right from wrong well (precision of `unique` is 44–61 %). A null test asks a different
question: does the *real* timing fit better than a *wrong* timing would? That is evidence about chance
coincidence that the current labels don't use.

**Blind commitment.** On synthetic data the truth is known up front. On real recordings (milestones 4.5 and 7)
the truth must not influence the result, and a hash makes that checkable. CLAUDE.md's Operating modes rule:
nothing chosen after looking at the truth.

## 3. Code map (cli.py on main at cbc51ef; line numbers approximate)
| What | Where |
|---|---|
| CLI inputs from `--scenario` / `--audio` etc. | `locate_inputs` (~553) |
| Result container | `LocateResult` dataclass (~568) |
| The whole locate run, in memory | `run_locate(cfg, inp, *, fits=None, audio_data=None, sink_factory=None)` (~613) |
| Events: detection and fit (or `fits=` passed in) | `run_events` call (~647) |
| Events, Δt prior, TrackSet, grid spec, cascade params | `cascade_inputs(...)` (~663) |
| **Truth already read here** for diagnostics (event ↔ truth matching) | ~688 |
| Cascade loop over tiles | `cascade.iter_cascade(...)` (~719) |
| Scoring and ranking | `score.rank_cells(...)` (~795) |
| Truth evaluation (rank, error, outcome tier via `doppler/evaluate.py`) | from `truth_eval = None` (~836) |
| Files and `run.json` | `write_outputs(res, args, T0)` (~1010); `run.json` written ~1160 |
| CLI command | `cmd_locate` (~1197) |

Notes:
- **`fits=` lets a caller pass pre-computed events.** A null test can shift the events' received times and call
  the cascade and scoring path again, without re-running detection. Each shifted search costs about as much as
  the original cascade plus scoring. In the README demo, scoring took 21 s of a 36 s run.
- **How to shift.** Moving the events against the ADS-B is equivalent to moving the recording start. Either
  shift each event's received times by `s`, wrapping within the clip, or shift the time prior's start. Wrapping
  matters: an event shifted past the end re-enters at the start.
- **Line ~688 reads the truth before scoring,** to match events to truth events for diagnostics. Check that
  nothing truth-derived feeds the search. Compute the commitment over search outputs only, and make sure the
  commitment is computed before anything truth-derived is *reported*.
- `run_locate` is also what the ablation driver calls in-process (`cli.py ablate`). With the features off, its
  behaviour and its return value must not change.

## 4. Rules for this task
- **Off by default.** With the features off, `locate` output must be bit-identical to main: `run.json`
  candidates, scores and Δt, `results.gpkg` layers and `score.tif`. Verify on at least 5 synthetic scenarios
  (e.g. `cli.py synth --clean --seed N --event-times 150,420 --duration-s 600 --cascade-decoys 3` for a few N,
  plus one realistic seed without `--clean`), and report the check in the PR. Decision runs compare against the
  Stage A, U18 and U19 rows, which assume the V0/V1 code paths are unchanged (CONTRIBUTING §3).
- **Not yours to edit:** `doppler/evaluate.py`, the `ablate` code in `cli.py`, `doppler/score.py` internals,
  `doppler/detect.py`, `doppler/ridge.py`, `reports/apriori_registry.md`, `reports/u19.md`, `HANDOFF.md`. The
  U19 detection session (on another machine) works there. Its next step is reports only; after that it edits
  `detect.py`, `ridge.py` and their config sections.
- **Pure logic in a new module** (e.g. `doppler/nulltest.py`), with tests. File I/O stays in `cli.py` /
  `export.py`.
- **Config.** Every new setting goes in a new `diagnostics:` section at the end of `config.yaml`, with a matching
  dataclass. `tests/test_config.py` requires every dataclass field to be in config.yaml and vice versa.
- **Report only.** Record the numbers in `run.json`. Add no pass/fail threshold and no change to status labels or
  ranking, so no registry entry is needed yet. Wiring the null tests into ablation rows (about 3 extra searches
  per run) is a later decision for the user.
- **Tests.** The CI gate is `pytest -m "not slow and not heavy" -n 4` (about 30 s). A test slower than about 2 s
  on the runner gets `@pytest.mark.heavy` (CONTRIBUTING §7).
- **Writing rule.** No personal or machine details in files or commit messages (CLAUDE.md Conventions).
- **Workflow.** Commit per logical step, push the branch, open a PR, and merge only with the user's OK. If the
  U19 session's code has merged first, rebase; expect small conflicts at most, in `cli.py` and `config.yaml`.

## 5. Suggested output fields in `run.json` (a proposal; adjust if the code suggests better)
```json
"null_test": {
  "shifts_frac": [0.25, 0.5, 0.75],
  "real": {"best_score": 1.03, "n_explained": 2},
  "shifted": [{"shift_s": 150.0, "best_score": 3.9, "n_explained": 1, "n_candidates": 40}, "..."],
  "best_shifted_over_real": 3.8
},
"result_commitment": {"sha256": "…", "fields": ["rank", "lat", "lon", "score", "dt_s", "tracks"], "top_k": 5}
```
- **Null-test comparison:** explained events first, then score, consistent with the lexicographic ranking (more
  events explained always beats fewer).
- **Commitment:** a canonical serialisation, e.g. JSON with sorted keys and fixed float formatting, so the hash
  can be reproduced.
- **Checking it later:** a small way to verify a saved result against its hash, such as a `cli.py verify-result`
  subcommand or a function in `export.py`.

## 6. Done means
- Both features are off by default, and the bit-identity check is reported.
- Tests pass, and the new tests cover shift wrapping, hash reproducibility, and verification failing on a
  tampered result.
- A short `docs/` note (this file can be updated in place) explains how to read the null-test numbers, with one
  real example from a synthetic scenario. Include one where a decoy or wrong answer shows a small
  real-to-shifted gap, if you can find one.
- A PR is open; it is not merged without the user's OK.
