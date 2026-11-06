"""Stage A2 cross-check (NEXT_SESSION task B step 3): on all-on C seeds 0-49, the A2 V0 / V1 rows must equal the
U19 decision run's V0b / V1b rows on every result field. Allowed exceptions: runs whose timeout status changed.

    .venv/bin/python reports/experiments/stageA2_crosscheck.py <stageA2 rows.jsonl> [reports/data/u19_rows.jsonl.gz]
"""
from __future__ import annotations

import gzip
import json
import sys

# provenance and wall time, not results
NOT_RESULT = {"git", "config_sha256", "seconds", "timings_s", "variant"}


def load(path):
    op = gzip.open if path.endswith(".gz") else open
    return [json.loads(line) for line in op(path, "rt")]


def timed_out(r) -> bool:
    return r.get("failure") == "timeout" or "timeout" in str(r.get("status", "")) or "timeout" in str(r.get("outcome", ""))


def main(a2_path: str, u19_path: str = "reports/data/u19_rows.jsonl.gz") -> int:
    a2 = {(r["variant"], r["seed"]): r for r in load(a2_path) if r["scenario"] == "C" and r["config"] == "all"}
    u19 = {(r["variant"][:2], r["seed"]): r for r in load(u19_path) if r["seed"] < 50}
    same = timeout_changed = 0
    other = []
    for key in sorted(a2):
        if key not in u19:
            continue
        x, y = a2[key], u19[key]
        if timed_out(x) != timed_out(y):
            timeout_changed += 1
            print(f"timeout status changed: {key[0]} seed {key[1]}: U19 {'timeout' if timed_out(y) else 'finished'}"
                  f" -> A2 {'timeout' if timed_out(x) else 'finished'}")
            continue
        diff = sorted(k for k in set(x) | set(y) if k not in NOT_RESULT and x.get(k) != y.get(k))
        if diff:
            other.append((key, diff))
        else:
            same += 1
    print(f"compared {same + timeout_changed + len(other)} runs: {same} identical, {timeout_changed} timeout "
          f"status changed, {len(other)} other differences")
    for key, diff in other:
        print(f"  DIFF {key[0]} seed {key[1]}: {diff}")
    return 1 if other else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
