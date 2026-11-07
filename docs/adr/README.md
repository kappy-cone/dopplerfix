# Architecture decision records

One file per decision: context, options, the choice and its consequences. Status: Proposed → Accepted
(or Rejected / Superseded). They started from the open items in `reports/tech_debt_audit.md`; CONTEXT.md has
the performance history behind 0004–0006.

| ADR | Decision (all accepted 2026-11-06) | Audit item |
|---|---|---|
| [0001](0001-environment-pinning.md) | **Accepted.** Record Python and package versions in every run; no transitive lock | 6 |
| [0002](0002-lint-in-the-gate.md) | **Accepted.** ruff with pyflakes rules only in the merge gate; fix the 15 findings | 9 |
| [0003](0003-parquet-engine.md) | **Accepted.** Stay on fastparquet until pyopensky or a pandas 4 bump | 13 |
| [0004](0004-trackset-memory.md) | **Accepted.** No TrackSet layout change for the 1-min benchmark; measure; chunk by time or two-tier for long clips; no ragged layout | 8 |
| [0005](0005-locate-stage-functions.md) | **Accepted.** Split `run_locate` into pure stage functions in `doppler/pipeline.py`, one bit-identical PR per stage | 3 |
| [0006](0006-lockstep-centre-scoring.md) | **Accepted.** Centre scoring in lockstep per cell, grouped by (event, track); identical results | 4 |
