# Testing strategy: structure, gaps and plan (2026-11-06)

Inputs: the architecture review of test and experiment cost (2026-11-05, at f6baa69; candidates A–E plus quick
wins), the current suite on main, a line-coverage run of the merge gate (coverage.py, installed outside the
repo, nothing added to requirements), and this session's heavy and slow runs. Related: CONTEXT.md (performance
rules), docs/adr/0004–0006.

## 1. The current structure

| Layer | What | Count | Time | When |
|---|---|---|---|---|
| Unit / component | physics, geo, tracks, cascade stages, scoring pieces, config coverage, CI shard, experiment imports | 266 (250 when this plan was written) | 54 s on 4 workers (85 s serial with coverage); CI about 30 s over 6 shards | every push (gate) |
| Integration (`heavy`) | true cell ranks 1st ×4, the frozen scoring oracle, the null-test ranking | 6 | 0.5–4 min each | after merge |
| End to end (`slow`) | locate contract, events seam, synth → fit recovery (11 seeds), scenario/decoy checks, ablate smoke, the Wilks Monte Carlo, the correlated-noise xfail | 27 | 10 min 45 s locally on 4 workers; about 14 min on CI | after merge |
| System | ablation / decision runs against reference rows (Stage A2) | – | 50–90 min | by hand, on the compute machine |

The shape is right: many fast unit tests, a few expensive scenario tests, and decisions made on reference rows.
Three structural problems sit on top of it:
1. **The gate skips the per-event pipeline**, the code that changes most (§3, G1).
2. **The bit-identity rule has no automated check**: every "results identical" claim (#15, ADR-0005, ADR-0006)
   rebuilds units by hand (G2).
3. **Expensive fixtures are rebuilt per test and per worker** (the review's C), so the after-merge job grows
   with every scenario test.

## 2. Status of the architecture review's candidates

| | Candidate | Status | Here |
|---|---|---|---|
| A | Deepen locked scoring | **Done in part:** #15 batched refinement. The per-cell cost table is ADR-0006 (proposed, not started) | Not repeated; T6 adds its test |
| B | Scenario world first, audio on demand | Not started | T5 |
| C | One test-side stage store | Not started | T3 |
| D | Stage keys for ablation units | Not started; changes what a timeout measures, so only with a new reference | Out of scope (production, not tests) |
| E | Pre-registered Monte Carlo checks only on change | Not started | T4 |
| Quick wins | Null-test dedupe; xfail `run=False`; gate tests over 2 s; `loadgroup`; per-event SNR in rows; CONTRIBUTING §7 duration | Only the null-test **comment** fixed (#18) | T3, T4, T7 |

## 3. Coverage of the merge gate (line coverage, `-m "not slow and not heavy"`)

| Module | Gate coverage | Functions the gate never reaches |
|---|---|---|
| `fit.py` | **58 %** | `characterize_window` 57/58 lines missed, `characterize_audio` 29/30, `characterize_events`, `is_harmonic_of` |
| `ridge.py` | **67 %** | `doppler_hough` 62/63, `guided_ridge` 11/12 |
| `classify.py` | **68 %** | `harmonic_count`, `decide`: event prop/jet labelling, no test file of its own |
| `nulltest.py` | 76 % | `search` |
| `export.py` | 75 % | `write_events` / `read_events` (the P0.3 seam), `write_scenario` |
| `geo.py` | 87 % | `RasterDEMSource`-style windowed reads in part; geoid grid test skipped where `data/proj` is missing |
| `plot.py` | 30 % | viewer and plots; diagnostics only |
| `cli.py` | 37 % | subcommand bodies, `write_outputs`, the ablation harness |
| cascade, score, tracks, synth, realism, physics, adsblol | 93–99 % | – |

What this means: the slow suite exercises spectrogram → detection → ridge → fit → classify only after merge.
These are exactly the files the U19 arms changed (`ridge.py`, `fit.py`), and the parameters CLAUDE.md marks as
provisional. A break there reaches main and is found 10–15 min later. Since a 60 s, two-event synthetic clip
runs the full event pipeline in **about 2 s** (measured), the gate can afford one.

`plot.py` and `cli.py` coverage is low by design (I/O and display). Their contracts are covered by the slow
locate tests. A coverage target of 100 % there would mostly test matplotlib.

## 4. The plan, in priority order

### T1. A gate test of the event pipeline (gap G1); about 2 h
- **What.** One short synthetic clip (60 s, 2 straight passes, SNR 10 dB, seed fixed) through
  `detect` → `characterize_audio` → `classify`, in the gate. Assert:
  - both events are found and none are spurious;
  - v within 5 %, d within 15 %, t0 within 1 s (the slow suite's own acceptance bounds);
  - the class label is `prop`;
  - σ is finite.
- **Second case.** A jet-like broadband pass, so `classify.decide`'s other branch runs. Plus a direct
  `classify.decide` table test for the boundary cases (unknown, harmonic count at its threshold).
- **Type.** Integration, in the gate. Expected cost about 2–4 s per case.
- **Why first.** It is the largest uncovered risk, and the slow suite already has the acceptance bounds.

### T2. Golden-row regression test for the bit-identity rule (gap G2); about ½ day
- **What.** Choose 3 Stage A2 units with clear margins: one V1 `unique` correct, one `ambiguous`, one V0
  `none`. Rebuild them from their seeds and compare with `reports/data/stageA2_rows.jsonl.gz`:
  - **discrete fields exactly:** status, outcome, n_events, survivors per stage, rank1 assignment,
    true_cell_rank, n_hypotheses_scored;
  - **floats** exactly where the geoid grids (`data/proj/`) are installed, as on the compute machine that
    made the rows; rel 1e-6 elsewhere (CI), since CONTEXT.md puts the 7th-digit differences across machines
    down to the grids. The test reports which mode it ran in.
  - **never compared:** provenance and run-time fields (`git`, `config_sha256`, `detection_sha256`,
    `seconds`, `timings_s`, disk footprint). #17 and #18 moved both hashes with results unchanged, so a hash
    check would fail without any result change.
  - **"clear margins"** also means far from the 900 s ablation cap: pick units that finish in well under a
    minute in the A2 rows, so a slow runner cannot turn one into a timeout.
- **Type.** System-level regression, `heavy`, about 1–2 min.
- **Why.** ADR-0005 (five refactor PRs) and ADR-0006 each promise "identical results". Today each PR would
  rebuild units by hand. This makes the promise checked on every merge, and it catches accidental
  result-changing edits anywhere in the pipeline.
- **Rule.** When a result-changing change is accepted and a new reference run is made, the golden units are
  refreshed from it, in the same PR as the new rows. The test docstring says so.

### T3. Build each expensive fixture once (review C and the `loadgroup` quick win); about ½–1 day
- **Now:** `xdist_group` on the module-scoped `scenario_dir` users in `test_locate.py`, and run the
  after-merge job with `--dist loadgroup`. One build instead of three (about 80 s × 2 saved).
- **Then:** `tests/stages.py`, a small session-level store that hands out a scenario, its events and its
  cascade survivors by spec, built once per run. Tests enter through the seams that already exist
  (`run_locate(fits=…)`, `locate --events`). It replaces the three ad-hoc builders: `conftest.make`, the
  `scenario()` helper in `test_cascade.py`, and `test_locate.scenario_dir`. No production change.
- **Measure:** the after-merge wall time before and after; target ≤ 8 min on CI.

### T4. Pre-registered Monte Carlo checks: change when they run, never what they check (review E); about 2 h
- `test_wilks_white_noise_chi2_3` (about 2.5–5.5 min) and the correlated-noise xfail (about 2 min) can only
  change when the scoring likelihood changes.
- Add a `calibration` marker, and move both out of the after-merge job into a job triggered by changes to
  `doppler/score.py`, `doppler/physics.py` or `config.yaml`'s `score:` section, plus a weekly scheduled run.
- Trial counts are unchanged (registry rule).
- The xfail stays as a pre-registered record: `xfail(run=False)` in the after-merge job.

### T5. Scenario world without audio (review B); about 1 day, production change
- `synth.make_world(...)` returns tracks, truth and draws; `render_audio(world)` is a separate step that
  consumes the same random draws.
- **The test that guards it:** for several seeds and both realism modes, `make_world` draws equal those
  inside a full `make_scenario`, and full scenarios stay bit-identical (`truth.json` and audio hash).
- Payoff: the geometry-only and realism-toggle tests skip audio (the review's 21 s gate test drops to about
  3 s), and `decision.py`'s SNR table takes seconds.

### T6. Tests that ADR-0006 needs (with that work)
- The Brent port's probe sequence equals scipy's on 200 random bounded problems (gate, fast).
- Lockstep scoring equals `score_hypothesis` on every `CellScore` field over a cascade's survivors. Extend
  the frozen oracle in `tests/_scoring_oracle.py`; heavy.
- T2's golden rows then confirm the whole pipeline.

### T7. Keep the gate honest (about 1 h)
- A `conftest.py` hook that **warns** in the gate log when a test without `heavy`/`slow` takes over 5 s,
  following the CONTRIBUTING §7 rule ("mark it heavy"). It warns rather than fails, so a slow CI runner
  doesn't block a merge.
- CONTRIBUTING §7: the after-merge duration is about 11–14 min, not 5–10. Update it after T3 and T4 land.
- The null-test heavy test: drop its duplicated reference cascade (shift 0 already reproduces it). About
  100 s locally becomes about 40 s. (The review's quick win; only its comment was fixed in #18.)

### Not recommended
- **A line-coverage target in the gate.** It adds a dependency (pytest-cov), and the low-coverage modules are
  I/O and display by design. Instead, rerun the coverage measurement above when the pipeline structure
  changes (ADR-0005), and keep the table in §3 current.
- **Property-based testing (hypothesis).** A new dependency. The physics invariants (frame independence,
  inversion round-trips, emission-time consistency) are already tested with seeded random draws.
- **Mocking DEM or ADS-B sources in integration tests.** The flat-DEM and synthetic tracks are real
  implementations of the same interfaces. Keep them.

## 5. Targets
- **Gate:** at most about 60 s locally on 4 workers and about 30 s on CI. Every module in `doppler/` that
  holds pipeline logic (not plot/export display code) at ≥ 85 % gate line coverage. Today
  fit/ridge/classify are at 58–68 %; T1 should lift them.
- **After merge:** at most 8 min on CI (T3 + T4), with T2's golden rows inside it.
- **Every "identical results" PR:** T2 green, plus the PR's own oracle test (T6 pattern).

## 6. Order alongside the ADRs
T1 → T2 (before ADR-0005's first PR and before ADR-0006, since both promise identical results) → T7 quick fixes → T3 → T4 → T6 (with
ADR-0006) → T5.

## 7. Brief for the implementing session (compute machine)
Paste into the session on the compute machine, run from the repo root, now that #18 and #19 are merged:

```
Pull first (git pull --rebase on main). Read CLAUDE.md, CONTRIBUTING.md §4, §4.1 and §7, CONTEXT.md, and
reports/test_plan.md (this plan), then docs/adr/0005 and 0006 for why T2 matters.

Implement reports/test_plan.md T1, then T2, one branch and one PR each, then STOP for me.
- T1: a gate test that runs a 60 s, 2-pass synthetic clip (seed fixed, SNR 10 dB, straight) through detect →
  fit.characterize_audio → classify, using the slow suite's bounds (v 5 %, d 15 %, t0 1 s, class prop, finite
  σ). Add a jet-like pass and a classify.decide table test. Keep it under about 5 s; check the gate time.
- T2: a heavy golden-row test. Pick 3 Stage A2 units with clear margins (one V1 unique correct, one ambiguous,
  one V0 none); say why each was chosen. Rebuild from their seeds, compare with
  reports/data/stageA2_rows.jsonl.gz: discrete fields exactly; floats exactly where data/proj/ has the geoid
  grids, rel 1e-6 elsewhere; never the provenance / run-time fields (git, config_sha256, detection_sha256,
  seconds, timings_s). Units well under a minute in the A2 rows. On this machine (where the rows were made)
  confirm the exact mode passes, and record that in the PR. The docstring says the
  units are refreshed in the same PR as any new reference run.
Rules
- Test-code PRs: no change to config.yaml or to any result. Don't move provisional values.
- Smoke-run rule doesn't apply (no long run), but don't run tests beside a decision or reference run.
- One branch per task, gate green, a PR. Don't push to main.
Stop point: after the T2 PR.
```
