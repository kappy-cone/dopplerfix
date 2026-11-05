# Report-only diagnostics: time-shift null tests and the blind result commitment

Two diagnostics for `cli.py locate`. Both are **off by default** and **report only**: they add blocks to
`run.json` and print one console line each. They never change the ranking, the status label or any other
output. Settings are in `config.yaml` → `diagnostics:`. The flags `--null-test` and `--commit-result` switch them
on for one run, and the run's `config` block records that they were on. Code: `doppler/nulltest.py`,
`doppler/commitment.py`, `export.verify_result`, and `cli.py` (`locate_diagnostics`, `verify-result`).

The ideas come from the method description of an independent tool,
[Espaye/aircraft-doppler](https://github.com/Espaye/aircraft-doppler) (`docs/method.md` §5–6). They are
reimplemented here; no code is shared.

## 1. Time-shift null tests (`null_test` in run.json)

**Question:** does the *real* timing of the events against the ADS-B fit better than a *wrong* timing would?

**What runs:** for each fraction f in `null_shifts_frac` (default 0.25, 0.5, 0.75), every event is moved by
f × clip duration. An event moves as a unit: its received CPA time becomes (t_rx + s) mod duration and its ridge
points move with it, so the curve keeps its shape. An event pushed past the end comes back in at the start. Then
the cascade and locked scoring run again with the same grid, Δt prior, tracks and parameters. Each shift costs
about one cascade plus scoring run.

**Fields:**

| field | meaning |
|---|---|
| `real` | the real search's rank 1: `n_explained` (events given a track), `best_score`, `dt_s`, `lat`/`lon`, `tracks`, `n_candidates`, `survivors` |
| `shifted[i]` | the same for shift i, plus `shift_frac`, `shift_s`, `ridge_span_rel_s` (where the shifted ridges sit relative to the clip) and `seconds` |
| `best_shifted` | index of the shift whose rank 1 ranks best |
| `explained_gap` | real `n_explained` − best shifted `n_explained`. Positive: the real timing explains more events than any shift does |
| `score_gap`, `best_shifted_over_real` | best shifted score − real score, and their ratio. Given only when both explain the same number of events (the ratio also needs a positive real score) |
| `n_shifted_not_worse` | how many shifts rank at or above the real rank 1 |

The comparison follows the ranking's own order: more explained events first, then the lower score.

**How to read it:**
- **Large separation** (positive `explained_gap`, or a ratio well above 1, with `n_shifted_not_worse` = 0):
  a wrong timing does not reproduce the fit, so a chance coincidence is unlikely.
- **Small separation** (ratio near 1, or `n_shifted_not_worse` ≥ 1): the tracks are dense enough that some
  assignment fits *any* timing about as well. The real result may still be right, but the fit alone does not
  show it.
- **No thresholds yet.** Where "small" ends is a calibration question for decision runs, not something set here.

**Caveats:**
- With three shifts the test is coarse. It shows whether coincidence is plausible; it is not a p-value.
- A shifted ridge can reach past the clip edges (`ridge_span_rel_s` outside [0, duration]). The TrackSet is
  rebuilt over the wider span from the same prepared tracks. Tracks are loaded for the clip plus margins
  (120 s + clock tolerance before, 60 s + clock tolerance after), so a ridge reaching beyond those margins can
  have points no track can model. Those points are penalised, which favours the real timing slightly.

### Examples (synthetic, 600 s clip, events at 150 s and 420 s, 3 cascade decoys)

Default config (V1), no geoid grid (constant-undulation fallback). Seeds 0–3 use `--clean`; the others use the
full realism layer. Scores are rank-1 scores (lower is better). "Shifts" lists explained events/score for the
0.25, 0.5 and 0.75 shifts ("–" means no candidate).

| seed | events | status | rank-1 error | real | shifts | explained gap | ratio | not worse |
|---|---|---|---|---|---|---|---|---|
| 0 | 2 | inconsistent | 36 m | 2 / 1.03 | – · 1 / 0.64 · – | 1 | – | 0 |
| 1 | 2 | inconsistent | 15 m | 2 / 1.06 | 2 / 27.6 · 2 / 72.5 · 1 / 2.39 | 0 | 26.0 | 0 |
| 3 | 2 | inconsistent | 14 m | 2 / 1.31 | 1 / 4.67 · 1 / 4.12 · 1 / 12.2 | 1 | – | 0 |
| 6 | 2 | unique | 38 m | 2 / 1.24 | 2 / 13.9 · 2 / 5.14 · 2 / 9.35 | 0 | 4.14 | 0 |
| 7 | 3 | unique | 24 m | 3 / 2.02 | 2 / 37.0 · 2 / 10.1 · 2 / 19.3 | 1 | – | 0 |
| 10 | 1 | ambiguous | 1803 m | 1 / 0.55 | 1 / 0.74 · 1 / 1.14 · 1 / 0.74 | 0 | **1.36** | 0 |
| 13 | 3 | inconsistent | 6879 m | 2 / 2.25 | 3 / 45.6 · 3 / 34.2 · 2 / 1.35 | **−1** | – | **3** |

How to read these:
- **Right answers separate clearly.** In every run where rank 1 is near the truth (seeds 0–7), no shift ranks
  at or above it. Either the shifts explain fewer events (seed 0: the best shift explains one event, the real
  timing two), or they explain as many at 4–26 times the score.
- **Seed 10, small gap.** Only one event was detected, so the answer is a locus 1.8 km from the truth. The best
  shift scores just 1.36 × the real one: one flyover can be fitted at almost any timing. This is the
  "coincidence is plausible" signature.
- **Seed 13, wrong answer.** The real rank 1 explains only 2 of 3 events with the wrong aircraft, 6.9 km off.
  Every shift does at least as well. Two shifts explain all three events, and the third explains two at a lower
  score (1.35 against 2.25). The real timing has nothing a wrong timing lacks, so the result should not be
  trusted. The status label (`inconsistent`) also abstains here, but for a different reason.
- Seed 12 (not shown: 4 events, no candidate in the real search or any shift) reports `n_shifted_not_worse` 0
  and no gaps.

Cost: the three shifted searches took 12–352 s per run here, roughly 1–4 times the real cascade plus scoring.

## 2. Blind result commitment (`result_commitment` in run.json)

**Purpose:** on real recordings the truth must not influence the result. The commitment fixes the result
before any reference is used and makes that checkable later.

**What is hashed:** the top `commit_top_k` candidates by rank. For each: rank, refined location (`est_lat`,
`est_lon`, 7 decimals), score (at the float32 precision `results.gpkg` stores, 9 significant digits), Δt
(3 decimals) and the assigned icao24 per event (`""` when unmatched). The total candidate count is hashed too.
The serialisation is canonical: JSON with sorted keys, no whitespace, every number as a fixed-format string. The
hash is SHA-256. Only search outputs go in; nothing derived from truth.

**When:** `cli.py locate` computes it right after the search. It prints it, before anything truth-derived is
reported (the truth summary, run.json's `truth_eval`). It is the same with or without a truth file
(`tests/test_locate.py` checks this).

**Checking a saved run:**

```bash
python cli.py verify-result outputs/<run>
python cli.py verify-result outputs/<run> --sha256 <hash recorded elsewhere>
```

This rebuilds the payload from `results.gpkg` (the saved candidates) and compares its hash with the one in
`run.json`, and, if given, with a hash kept elsewhere. It exits 0 on a match and 1 on a mismatch (it lists the
ranks that differ from the recorded payload), or 2 if the run has no commitment. On its own, the hash in
`run.json` only catches edits to the candidates layer. It also catches an edit to `run.json` once the hash has
been recorded somewhere else before the truth was looked at (a message, a commit, a ticket), and is checked
with `--sha256`.

## 3. Off means unchanged

With both diagnostics off, `locate` writes the same candidates, scores, Δt, `results.gpkg` layers and
`score.tif` as before. `run.json` differs only in its `config` dump, which now includes the `diagnostics:`
section. The PR that added these diagnostics reports the check on seven synthetic scenarios.
