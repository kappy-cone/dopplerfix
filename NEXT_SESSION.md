# Prompt for the next session (session 8 continued: task B, the post-U19 reference run)

Paste the block below as the first message of the session, run from the repo root on the compute machine.

```
Pull first (git pull --rebase on main), then read CLAUDE.md (incl. the personal-details rule and the smoke-run
rule under Validation), CONTRIBUTING.md §4, §4.1 and §7, and in reports/u19.md "Decision run, attempt 2" and
the arm-b default section.

What task B is
- A new full reference run, "Stage A2": the stageA ablation plan with V0 and V1 under the current defaults.
  275 units: all-on C seeds 0-49, each leave-one-out / subgroup config × seeds 0-19, clean × seeds 0-4, and the
  four skeptic configs × seeds 0-9.
- Why it is needed: the Stage A and U18 rows (reports/data/) were made with the pre-U19 detector. Since arm b
  became the default, later comparisons on any config other than all-on C need a reference made with the
  current code. The U19 rows cover only all-on C seeds 0-99.
- What it is for: the baseline for U19 task 4 (arm h follow-up), for the two-stage refinement arm, and for the
  targeted methodology review (a current failure taxonomy: survival / discrimination / bias / detection).
- What it is not: a decision run for an arm. Nothing is accepted or rejected, and no config value changes.

Prerequisites on main (check before anything else; stop and tell me if either is missing)
- PR #14 merged: config.yaml has detect.long_window_s: 3.0 and synth.scenario_detection pinned to the pre-U19
  detector (`grep -n long_window_s config.yaml`).
- PR #15 merged (f809b80): bit-identical batched scoring. It changes no result except runs that used to hit
  the 900 s cap and may now finish.

Run (clean main, this machine, the same one as the Stage A / U18 / U19 rows)
1. Smoke run (CONTRIBUTING §4.1): the command below with --limit 3 into outputs/smoke/stageA2. Check:
   - V0 / V1 rows record arm b's detection key (the U19 V0b / V1b key), not the pre-U19 one;
   - each smoke unit's truth.json equals Stage A's (the scenario pin works);
   - rows complete, commit and config hash as expected, outputs/ablation/stageA2 does not exist yet.
   Don't read outcomes. Record one line in the report.
2. The run, into a fresh folder (ablate resumes from an existing rows.jsonl):
     .venv/bin/python cli.py ablate --plan stageA --variants V0,V1 --jobs 8 --out outputs/ablation/stageA2
     .venv/bin/python cli.py ablate --summarize --out outputs/ablation/stageA2
   Stage A took about 70 min for these 275 units at --jobs 8; #15 should make it shorter.
3. Cross-check (it also verifies #15 on this machine): on all-on C seeds 0-49, the new V0 / V1 rows must equal
   the U19 V0b / V1b rows (reports/data/u19_rows.jsonl.gz) on every result field. The only allowed exceptions
   are runs whose timeout status changed; list each one. Any other difference: stop and investigate.
4. Commit the rows gzip-compressed as reports/data/stageA2_rows.jsonl.gz. Write a section in reports/stageA.md:
   the same §8.1 measures and failure taxonomy as Stage A, paired against the pre-U19 Stage A rows, plus the
   smoke-run line, the cross-check result and timeouts against Stage A's.
5. Branch s8-stageA2 in a separate worktree while the run uses the main checkout, a PR, then STOP for me.

After task B (each only after my OK)
- U19 task 4 follow-up: arm h1 recovers events (49 / 0 pooled) but doubles spurious events. A spurious-fit
  filter is a new knob: propose it, register it before measuring, screen against the Stage A2 baseline.
- Two-stage refinement (full L-BFGS-B only for the top K cells by start-grid score): a result-changing speed-up,
  registered with K fixed in advance, decided against the Stage A2 rows.
- The targeted methodology review (validity checks that can pass vacuously, plan order, what the real adsb.lol
  day changes).

Rules
- One branch per task, commits with the gate green, a PR. Don't push to main without asking.
- Decision and reference runs only from clean main. Never edit code in a checkout while a run uses it.
  Smoke run before every long run. Screens only rule ideas out; never re-run just the seeds that failed.
- Keep V0 and V1 runnable and compared (V0-preU19 / V1-preU19 keep the old detector). Never move
  detect.trigger_db, ridge.weak_db or any provisional value. Never tune on synthetic data.
- Config items still waiting for me: aircraft_types.type_table_path, tracks.drop_onground.

Stop point: after task B (the PR with the rows and the report section).
```
