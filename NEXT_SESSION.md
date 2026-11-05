# Prompt for the next session (session 7, U19 detection: step 3 onward)

Paste the block below as the first message of the session, run from the repo root on the compute machine.

```
Pull first (git pull --rebase on main), then read, in order:
- CLAUDE.md (incl. the personal-details rule in Conventions) and CONTRIBUTING.md (§1 branches/PRs, §4 decision
  runs, §7 CI: the ~30 s gate on every push, heavy + slow after merge);
- HANDOFF.md START HERE;
- reports/u19.md (steps 1-2 and the brainstorm at its end);
- docs/u19_detection_literature.md (D1-D5);
- reports/combined_plan.md §8.2-8.5, and the U18 section at the end of reports/stageA.md.

State on main:
- U19 arms in doppler/detect.py, off by default (V0/V1 rows bit-identical): (a) harmonic-summed row,
  (b) 3 s long-window rows, (c) 20-100 Hz low-band row. Knobs in reports/apriori_registry.md.
- Step 2 screen (detection only, seeds 0-49, six configs, paired by audio): only arm b matters. All-on recall
  75 -> 82/100 (7 gained, 0 lost, p = 0.016), loo:path +13, sub:reflection +10, spurious per run not up.
  Arm a: no effect. Arm c: loo:path only. No arm recovers the 14 all-on misses lost after detection.
- Brainstorm findings (reports/u19.md):
  - F1: recall falls off a cliff below ~6 dB band SNR, and half the synthetic events sit there;
  - F2: 6 of the 14 lost-after-detection misses look like mis-timed fits;
  - F3: perfect detection would lift V1 rank-1 accuracy from 34 to about 48/100 at most, and arm b alone by
    only a few runs.
- Screen harness: reports/experiments/u19/screen.py and summarize.py. Screen rows stay in outputs/.

Tasks, in order:
1. D1 + E1 diagnostics (reporting only, no knob):
   - D1: classify each "window but no matched fit" miss by where it was lost (ridge returned nothing, fit
     rejected, fit > 10 s from the truth, removed by dedupe / harmonic check, too slow), and arm b's remaining
     window-less misses.
   - E1, oracle ladder: truth windows given as manual windows, then truth-derived ridge points. Diagnostics
     only, never a run claim.
   - Same six configs, seeds 0-49, base and arm b. Add recall against band SNR (E3) to summarize.py.
   - Write the results into reports/u19.md. Commit, push the branch.
2. Step 3: write a pre-registered acceptance rule for arm b into reports/u19.md, then STOP for my OK.
   - Variants: V1b = V1 + detect.long_window_s 3.0, and V0b likewise (V0 stays compared).
   - Starting point to refine (justify every number a priori; nothing tuned):
     - all-on detection recall on seeds 0-99 up, exact paired sign test on truth events;
     - spurious events per run not up by more than a bound you propose (base all-on 0.52);
     - V1b rank-1 accuracy (status ignored) on all-on C n = 100 not lower than V1's 34; by F3 a gain can't be
       demonstrated at this n, so it is a non-inferiority condition;
     - report timeouts, answer rate, precision of unique, conditional accuracy and the detection class
       (the §8.1 measures).
   - Adding the V1b / V0b overlays changes config.yaml. Do it in this step, before the decision run, so the
     config hash stays stable from then on.
3. After my OK and the merge: the decision run from clean main. Run V1b and V0b on all-on C seeds 0-99, paired
   with the Stage A + U18 rows (reports/data/). Report the §8.1 measures and recall against SNR.
4. Only then, and only if D1/E1 point there: build D2 (path-level ridge acceptance), E2 (fuse the 3 s excess
   into the ridge DP on plateaus) or D3 (Doppler-Hough detection) as new arms. Each follows the same rules:
   - registered before its first measurement;
   - off by default;
   - screened on the six configs, seeds 0-49, paired by audio.

Rules:
- Branches and merges: one branch (s7-u19-step3), commits per step with the gate green, a PR. Don't push to
  main without asking.
- Runs: decision runs only from clean main. Never edit code in a checkout while a run uses it (use a
  worktree). Screens only rule ideas out; never re-run just the seeds that failed.
- Values: keep V0 and V1 runnable and compared. Never move detect.trigger_db, ridge.weak_db or any
  provisional value. Never tune on synthetic data.
- Stop and ask before any change to a CLAUDE.md methodology step beyond U18/U19.
- Config items that wait for me until after U19's decision run:
  - aircraft_types.type_table_path: data/doc8643_types.csv (real tracks only);
  - whether to add tracks.drop_onground (a config key and a TrackParams field together).

Stop points: after task 1 (diagnostics), after task 2 (acceptance rule), after task 3 (decision run).
```
