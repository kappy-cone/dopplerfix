# ADR-0004: TrackSet memory: measure first; time-chunking or two-tier sampling for long clips, not a ragged layout

**Status:** Accepted (2026-11-06, by the project owner)
**Date:** 2026-11-06
**Deciders:** project owner (it amends a CLAUDE.md Known-pitfalls item)
**Audit item:** 8

## Context
`TrackSet` holds `xyz` and `vel` as (K, T, 3) float64 and `h_m` and `ground_speed_mps` as (K, T): 64 bytes per
track-sample. On top of that, `_packed` (#15) caches a (T−1, 12) copy of each track that scoring touches.
`cli.cascade_inputs` builds **one** TrackSet for the whole clip window: the clip, ± the Δt prior, plus
reach/c, plus 60 s of padding on each side. CLAUDE.md says: "Before the US-wide benchmark, chunk by time or
store per-track ragged arrays."

**Estimates (to be measured).** K assumes about 5,000 ADS-B aircraft airborne over CONUS at a busy hour. That
is an assumption; the first US-wide dry run gives the real K.

| Clip | Window | T at 0.25 s | Dense TrackSet |
|---|---|---|---|
| 1 min (the benchmark) | about 4 min | about 920 | **about 0.3 GB** |
| 10 min | about 13 min | about 3,100 | about 1.0 GB |
| 1 h | about 1 h 3 min | about 15,000 | about 4.8 GB |

**Who reads the dense arrays.** There are 17 `ts.xyz` and 4 `ts.vel` sites, mostly in `cascade.py`. They use
vectorized fancy indexing over all tracks: `ts.xyz[:, ::step][kk, tm[ss]]`, a KD-tree over
`ts.xyz[track, sample]`, and `refine_cpa` on (k, s) pairs. `score.py` and `plot.py` read one track at a
time through `sample` and `_packed`.

**What ragged would save.** A ragged layout saves only the NaN padding outside each segment's coverage. At US
scale, in a window of an hour or less, most airborne aircraft span most of the window (flights last hours), so
the saving is small, perhaps 30–50 % for a 1 h window and less for shorter ones. It would also turn the
cascade's vectorized gathers into segment-offset arithmetic at every one of those sites.

## Decision
1. **The 1-minute benchmark does not need a layout change.** About 0.3 GB fits. Amend the CLAUDE.md pitfall
   to: "Before clips longer than about 10 min at US scale …".
2. **Measure now, cheaply:** log `K`, `T` and the TrackSet's bytes in run.json (`timings` / `counts` style,
   no config key). The first US-wide dry run then replaces the estimates above.
3. **For long clips, when needed:** keep the dense layout and bound T instead. Either
   - **time-chunking**: the cascade and the silence filter process the clip in windows of a few minutes, each
     with its own TrackSet; or
   - **two-tier sampling**: a coarse TrackSet (for example 2 s) over the whole window for the prefilter and
     silence, and the fine 0.25 s set only for the tracks and event windows that hypotheses use.
   Choose between them with measured K and T, in their own ADR at that time.
4. **Reject the ragged layout:** high churn across the cascade's vectorized code, for a small saving at US
   scale.

## Options considered

### A. Ragged per-track arrays (offsets plus concatenated samples)
| Dimension | Assessment |
|---|---|
| Complexity | High: every `ts.xyz` / `ts.vel` gather in `cascade.py` is rewritten; `valid()`, `active_between` and `coverage` change |
| Saving | Small at US scale (aircraft span most of a short window); large only for many short segments in a long window |
| Risk | A result-path change; needs a bit-identity pass on rebuilt Stage A2 units |

### B. Time-chunking
| Dimension | Assessment |
|---|---|
| Complexity | Medium: the silence filter and offset filter must combine chunk results; the pass that straddles a chunk edge needs overlap (padding ≥ reach/c) |
| Saving | Bounds memory by chunk length, whatever the clip length |
| Risk | Results stay identical if the padding is right; testable on synthetic long clips |

### C. Two-tier sampling
| Dimension | Assessment |
|---|---|
| Complexity | Medium: `refine_cpa` refines on the fine tier on demand |
| Saving | About 8× on the full-window tier at 2 s |
| Risk | The coarse prefilter must stay conservative (its tolerance grows with the step); changes survivor counts unless the coarse tier is used only to pre-select |

### D. float32 positions
Rejected. CLAUDE.md keeps geometry float64: ECEF coordinates are about 6.4e6 m, and float32 resolves them to
about 0.5 m, too coarse for radial-velocity differences.

## Trade-off analysis
The memory problem scales with clip length, not with the US-wide region. Ragged attacks the padding, which
is small at US scale; chunking and two-tier attack T, which is what grows. No change is needed for the stated
benchmark, so the cheapest correct step now is to measure.

## Consequences
- Easier: the benchmark can proceed with no TrackSet work; scoring speed-ups (ADR-0006) don't collide with a
  layout change in `score.py`.
- Harder: long US-wide clips stay unsupported until B or C lands.
- Revisit: after the first US-wide dry run (real K), or when the first real clip is longer than about 10 min.

## Action items
1. [ ] run.json: `trackset: {K, T, bytes}`.
2. [x] CLAUDE.md Known pitfalls: reword "Dense TrackSet" as in Decision 1, pointing here (docs refresh, 2026-11-06).
3. [ ] After the first US-wide dry run: replace the estimates above with measured values.
