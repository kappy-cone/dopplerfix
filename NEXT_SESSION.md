# Prompt to continue the PC session (session 6, U19 detection)

Paste the block below into the PC session once any run in progress has finished.

```
Pull first (git pull --rebase on main), then read CONTRIBUTING.md §7 (new), reports/combined_plan.md
§8.2-8.4, and the U18 section at the end of reports/stageA.md.

What changed on main since your U18 commit (b984ce8). Two PRs, both reviewed by a separate session:

1. #1 CI (291164b): .github/workflows/tests.yml, .github/actions/setup-env/action.yml, CONTRIBUTING §7.
   - Actions moved to Node 24 versions; the runner is pinned to ubuntu-24.04.
   - Docs-only changes (*.md, reports/, docs/, doppler.txt) skip CI.
   - The fast suite runs in three parallel shards (about 3.5 min): the four
     test_true_cell_ranks_first_after_locked_scoring cases in two pairs, and everything else.
   - The slow suite runs only when started by hand:
     gh workflow run tests.yml --ref <branch>
   - §7: a red fast run blocks a merge, and you never mark a test slow or xfail to get green. Branch
     protection stays off until about 10 PRs pass without a false alarm.
   - No code changed.

2. #2 real-data groundwork (31ebc79): doppler/tracks.py, tests/test_opensky.py, and docs/field_recordings.md,
   opensky_ingest.md, pyopensky_dependency.md, uat_coverage.md.
   - normalize_frame now recognises raw OpenSky state-vector tables (columns time + lastposupdate) and types
     them: times become epoch seconds, flags become bool, squawk stays a string.
   - It adds a bool `onground` column, which is flagged but never filtered.
   - It refuses frames renamed by the traffic library (altitude/groundspeed/vertical_rate, possibly in ft or kt)
     unless they are mapped explicitly.
   - Alias keys now match case-insensitively.
   - V0/V1 are unaffected. Synthetic and ablation frames have no `time` column, so they never take the new
     path. The reviewer compared prepared tracks on main and on the PR: 42 comparisons, 0 differences.
     A test now pins this. Stage A and U18 rows remain valid references.
   - The cropped OpenSky aircraft DB exists only on the laptop's main checkout (data/aircraft_db.csv,
     data/doc8643_types.csv; gitignored). Synthetic scenarios always carry their own registry, so this
     cannot change synthetic results anywhere.

Working rules from here: one branch (s6-u19-detection), commits per step with the fast tests passing, and a PR
with CI green. Dispatch the slow suite on the branch before asking for a merge. The decision run happens
only from a clean main after the merge. U18 went straight to main with my OK; don't repeat that without
asking.

U19 task (plan §8.2, approved). Detection caps accuracy: 20 of the 50 all-on Stage A runs lose a truth event
(recall 75/100 on seeds 0-49, 81/100 on seeds 50-99), and those runs account for 8 of V1's 13
discrimination failures.
1. Build the §8.2 candidates as detection arms behind config flags, off by default in V0 and V1 (so the V0/V1
   rows stay bit-identical):
   (a) a harmonic-summed tonal statistic (prop harmonic stacks, as ridge.doppler_hough sums for seeding);
   (b) a long-window (2-4 s) spectrogram pass for slow props;
   (c) a lower-band pass below the 100 Hz recording high-pass.
   Don't move detect.trigger_db or any other provisional threshold. Every new knob goes into
   reports/apriori_registry.md, with its a-priori source, before its first measurement. Tests for each arm,
   on synthetic fixtures generated at test time.
2. Screen, on detection only: run event detection without the cascade on seeds 0-49. Use the configs clean,
   all-on, path, levels, scene and sub:reflection (§8.4). Report, paired by seed against Stage A:
   - recall (truth events detected / truth events);
   - runs with an event missed;
   - spurious events per run.
   Screens only rule ideas out, and never re-run just the seeds that failed.
3. Before any decision run, write a pre-registered acceptance rule into reports/stageA.md (or a new
   reports/u19.md) and stop for my OK. For example: all-on recall up (exact paired test), spurious events per
   run not up by more than an amount you propose, and V1 rank-1 accuracy (status ignored) on all-on C n = 100
   not lower than 34. More detections mean more hypotheses and timeouts, so report timeouts and the answer
   rate too.
4. After my OK and the merge: the decision run from clean main, paired with the Stage A + U18 rows. Report the
   §8.1 measures, including conditional accuracy and the detection class.

Housekeeping, in its own small commit:
- update HANDOFF (START HERE for session 6; the fast suite now takes about 3-4.5 min locally, not ~45 s).
- Leave config.yaml alone until U19's decision run is done, so the config hash in the rows stays stable.
- After that, two config items wait for me:
  - aircraft_types.type_table_path: data/doc8643_types.csv (affects only real tracks);
  - whether to add tracks.drop_onground (needs a config key and a TrackParams field together; test_config
    requires both).

Stop points: after step 2 (screen results), and after step 3 (acceptance rule).
```
