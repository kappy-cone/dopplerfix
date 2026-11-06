"""U19 arm (b) decision analysis, fixed before the decision run (reports/u19.md, "Step 3: acceptance rule").

Pairs the decision run's V0b / V1b rows (all-on C, seeds 0-99) with the reference V0 / V1 rows of Stage A
(seeds 0-49) and U18 (seeds 50-99) in reports/data/, checks the run's validity, applies criteria C1-C3 and
prints the reported measures. Nothing here feeds back into a run.

    .venv/bin/python reports/experiments/u19/decision.py --rows outputs/ablation/u19/rows.jsonl [--no-snr]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from cli import apply_variant, detection_key, load_config, section  # noqa: E402
from doppler import evaluate as ev  # noqa: E402

REF_FILES = ("reports/data/stageA_rows.jsonl.gz", "reports/data/u18_rows.jsonl.gz")
DET_FILES = ("reports/data/stageA_detection.json", "reports/data/u18_detection.json")
SEEDS = range(100)
PRE_U19 = "V1-preU19"     # the reference's detection, once arm b became the default (session 8)
ALPHA = 0.05                 # C1: two-sided exact sign test
SPURIOUS_RATIO = 1.25        # C2: upper limit of the conventional 0.80-1.25 equivalence range
SNR_EDGES = (-float("inf"), 1, 2, 3, 4, 6, 8, float("inf"))
SNR_LABELS = ("< 1", "1-2", "2-3", "3-4", "4-6", "6-8", ">= 8")


def load_rows(rows_path: str):
    ref = [json.loads(line) for f in REF_FILES for line in gzip.open(ROOT / f, "rt")]
    new = [json.loads(line) for line in open(rows_path)]
    by = {}
    for r in ref + new:
        if r["scenario"] == "C" and r["config"] == "all" and r["seed"] in SEEDS:
            by[(r["variant"], r["seed"])] = r
    det = {d["seed"]: d for f in DET_FILES for d in json.load(open(ROOT / f))
           if d["scenario"] == "C" and d["config"] == "all"}
    return by, det


def event_detection(by, v_strict: str, v_robust: str, seed: int, det: dict):
    """Per truth event: detected? Detection does not depend on the cascade variant, so it comes from the strict
    row (V0 / V0b: no timeouts at n = 100 in U18), else the robust row. A row without per_event and with no
    fitted events means every truth event was missed (truth count from the committed detection counts: the
    scenario is the same for every variant). None if neither row can say."""
    for v in (v_strict, v_robust):
        r = by.get((v, seed))
        if r is None:
            continue
        if r.get("per_event"):
            return [e["detected_event"] is not None for e in sorted(r["per_event"], key=lambda e: e["truth_event"])]
        if r.get("n_events") == 0:
            return [False] * (r.get("n_truth_events") or det[seed]["n_truth_events"])
    return None


def spurious(by, v_strict: str, v_robust: str, seed: int):
    for v in (v_strict, v_robust):
        r = by.get((v, seed))
        if r is None:
            continue
        if r.get("extra_events") is not None:
            return r["extra_events"]
        if r.get("n_events") == 0:
            return 0
    return None


def same_detection(a: dict | None, b: dict | None) -> bool:
    """Fitted events identical as far as rows can tell (counts and every per-event residual, exactly)."""
    if a is None or b is None or a.get("n_events") != b.get("n_events") or a.get("extra_events") != b.get("extra_events"):
        return False
    pa, pb = a.get("per_event") or [], b.get("per_event") or []
    keys = ("truth_event", "detected_event", "dv_mps", "dd_m", "dt_rx_s")
    return len(pa) == len(pb) and all(all(x.get(k) == y.get(k) for k in keys) for x, y in zip(pa, pb))


RESULT_FIELDS = ("outcome", "status", "rank1_error_m", "rank1_assignment_correct", "true_assignment_group_rank")


def arm_applied(by, cfg: dict) -> dict:
    """V0 (added in session 8, after the first decision run turned out not to apply the arm): every non-timeout
    V0b / V1b row records the detection config it ran with (``detection_sha256``), that config is the arm's own,
    and it differs from the reference's. Rows from before the fix record nothing and fail."""
    want = detection_key(apply_variant(cfg, "V1b"))
    rows = [r for (v, s), r in by.items() if v in ("V0b", "V1b") and r.get("outcome") != "timeout"]
    unrecorded = sum(r.get("detection_sha256") is None for r in rows)
    wrong = sum(r.get("detection_sha256") not in (None, want) for r in rows)
    ok = (bool(rows) and unrecorded == 0 and wrong == 0 and want == detection_key(apply_variant(cfg, "V0b"))
          and want != detection_key(apply_variant(cfg, PRE_U19 if PRE_U19 in (cfg.get("variants") or {}) else "V1")))
    return {"ok": ok, "rows": len(rows), "unrecorded": unrecorded, "wrong": wrong}


def validity(by):
    """V1: the arm's strict and robust rows agree on detection. V2: runs whose fitted events equal the
    reference's reproduce the reference result fields exactly, timeouts aside (the V0/V1 code paths unchanged)."""
    out = {"detection_disagree": [], "identical_runs": 0, "identical_mismatch": []}
    for s in SEEDS:
        a, b = by.get(("V0b", s)), by.get(("V1b", s))
        if a and b and a.get("per_event") and b.get("per_event") and not same_detection(a, b):
            out["detection_disagree"].append(s)
        for vr, va in (("V0", "V0b"), ("V1", "V1b")):
            r0, r1 = by.get((vr, s)), by.get((va, s))
            strict_ref, strict_arm = by.get(("V0", s)), by.get(("V0b", s))
            if not same_detection(strict_ref, strict_arm):
                continue
            out["identical_runs"] += vr == "V1"
            if "timeout" in (r0.get("outcome"), r1.get("outcome")):
                continue
            if any(r0.get(k) != r1.get(k) for k in RESULT_FIELDS):
                out["identical_mismatch"].append((vr, s))
    return out


def _snr_one(seed: int) -> list:
    from screen import build_scenario

    _, sc = build_scenario("all", seed, "C")
    return [(seed, i, t.icao24, (sc.aircraft_info.get(t.icao24) or {}).get("peak_band_snr_db"))
            for i, t in enumerate(sc.truth)]


def snr_by_event(seeds, by, jobs: int):
    """Peak band SNR per (seed, truth event), from the rebuilt scenario (deterministic; no audio written). The
    truth-event index is checked against the rows' per_event icao24."""
    from multiprocessing import Pool

    with Pool(jobs) as pool:
        got = [x for part in pool.map(_snr_one, list(seeds)) for x in part]
    out = {}
    for s, i, icao, snr in got:
        for v in ("V0", "V1", "V0b", "V1b"):
            pe = {e["truth_event"]: e.get("icao24") for e in (by.get((v, s)) or {}).get("per_event") or []}
            if i in pe and pe[i] != icao:
                raise SystemExit(f"truth event order differs from the rows: seed {s} event {i} ({v})")
        out[(s, i)] = snr
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", required=True, help="the decision run's rows.jsonl (variants V0b, V1b)")
    ap.add_argument("--no-snr", action="store_true", help="skip the recall-against-SNR table (no scenario rebuild)")
    ap.add_argument("--jobs", type=int, default=8, help="processes for the scenario rebuild")
    a = ap.parse_args(argv)
    cfg = load_config(str(ROOT / "config.yaml"))
    ep = ev.EvalParams.from_dict(section(cfg, "evaluate"))
    by, det = load_rows(a.rows)
    missing = [(v, s) for v in ("V0", "V1", "V0b", "V1b") for s in SEEDS if (v, s) not in by]
    print(f"rows: {len(by)}; missing {len(missing)} {missing[:6]}")
    commits = sorted({(r.get("git") or {}).get("commit", "?")[:9] + (" dirty" if (r.get("git") or {}).get("dirty")
                                                                        else "") for (v, s), r in by.items()
                      if v in ("V0b", "V1b")})
    print(f"arm rows recorded commit(s): {commits}")

    print("\n## Validity (checked before any criterion is read)")
    app = arm_applied(by, cfg)
    print(f"- V0 arm applied: {'yes' if app['ok'] else 'NO'} ({app['rows']} non-timeout arm rows, "
          f"{app['unrecorded']} without a detection record, {app['wrong']} with another detection config)")
    if not app["ok"]:
        print("\n**INVALID RUN: the arm's detection config is not shown to have been applied. Criteria not read.**")
        return 2
    val = validity(by)
    print(f"- V1 detection, V0b vs V1b rows disagree on seeds: {val['detection_disagree'] or 'none'}")
    print(f"- V2 runs with the reference's fitted events: {val['identical_runs']}; result fields differ (timeouts "
          f"aside) on: {val['identical_mismatch'] or 'none'}")

    # C1: recall, exact paired sign test on truth events
    gained = lost = 0
    halves = {"0-49": [0, 0], "50-99": [0, 0]}
    unpaired = []
    for s in SEEDS:
        d_ref, d_arm = event_detection(by, "V0", "V1", s, det), event_detection(by, "V0b", "V1b", s, det)
        if d_ref is None or d_arm is None or len(d_ref) != len(d_arm):
            unpaired.append(s)
            continue
        h = "0-49" if s < 50 else "50-99"
        for x, y in zip(d_arm, d_ref):
            gained += x and not y
            lost += y and not x
            halves[h][0] += x and not y
            halves[h][1] += y and not x
    sign = ev.paired_sign([True] * gained + [False] * lost, [False] * gained + [True] * lost)
    n_ev = sum(len(event_detection(by, "V0", "V1", s, det) or []) for s in SEEDS if s not in unpaired)
    rec = {v: sum(sum(event_detection(by, *v, s, det) or []) for s in SEEDS if s not in unpaired)
           for v in (("V0", "V1"), ("V0b", "V1b"))}
    c1 = gained > lost and sign["p"] < ALPHA
    c1b = halves["50-99"][0] >= halves["50-99"][1]
    print("\n## Criteria")
    print(f"- C1 recall: reference {rec[('V0', 'V1')]}/{n_ev}, arm {rec[('V0b', 'V1b')]}/{n_ev}; events gained / lost "
          f"{gained} / {lost}, exact two-sided p = {sign['p']:.3g} -> {'PASS' if c1 else 'FAIL'}"
          + (f" (unpaired seeds {unpaired})" if unpaired else ""))
    print(f"- C1b seeds 50-99 alone (not screened): gained / lost {halves['50-99'][0]} / {halves['50-99'][1]} -> "
          f"{'PASS' if c1b else 'FAIL'}; seeds 0-49 (screened): {halves['0-49'][0]} / {halves['0-49'][1]}")
    sp_ref = sum(spurious(by, "V0", "V1", s) or 0 for s in SEEDS)
    sp_arm = sum(spurious(by, "V0b", "V1b", s) or 0 for s in SEEDS)
    c2 = sp_arm <= SPURIOUS_RATIO * sp_ref
    print(f"- C2 spurious events: reference {sp_ref} ({sp_ref / 100:.2f} / run), arm {sp_arm} ({sp_arm / 100:.2f} / run); "
          f"bound {SPURIOUS_RATIO} x {sp_ref} = {SPURIOUS_RATIO * sp_ref:.1f} -> {'PASS' if c2 else 'FAIL'}")
    r1 = {v: [ev.rank1_ok(by[(v, s)], ep) if (v, s) in by else False for s in SEEDS] for v in ("V0", "V1", "V0b", "V1b")}
    c3 = sum(r1["V1b"]) >= sum(r1["V1"])
    t = ev.paired_sign(r1["V1b"], r1["V1"])
    print(f"- C3 rank-1 accuracy (status ignored): V1 {sum(r1['V1'])}, V1b {sum(r1['V1b'])} -> "
          f"{'PASS' if c3 else 'FAIL'}; paired V1b only / V1 only {t['a_only']} / {t['b_only']} (p = {t['p']:.3g})")
    ok = c1 and c1b and c2 and c3 and not val["detection_disagree"] and not val["identical_mismatch"]
    print(f"\n**Verdict: {'ACCEPT' if ok else 'REJECT'} arm b** (C1, C1b, C2, C3 and validity all required)")

    print("\n## Reported measures (plan §8.1), all-on C n = 100")
    print("| variant | correct | rank-1 acc | answer rate | precision of unique [95 % CI] | confident-wrong | "
          "timeout | recall | detection class | cond. n | cond. rank-1 acc | cond. precision |")
    print("|" + "---|" * 12)
    summ = {}
    for v in ("V0", "V0b", "V1", "V1b"):
        rows = [by[(v, s)] for s in SEEDS if (v, s) in by]
        for r in rows:
            if r.get("n_truth_events") is None:      # Stage A rows don't record it; same scenario for every variant
                r["n_truth_events"] = det[r["seed"]]["n_truth_events"]
            if r.get("truth_events_detected") is None and not v.endswith("b"):   # reference timeout / no-event rows
                r["truth_events_detected"] = det[r["seed"]]["truth_events_detected"]
        if v.endswith("b"):
            for r in rows:   # arm timeout rows: detection from the strict arm row of the same seed
                if r.get("truth_events_detected") is None:
                    d = event_detection(by, "V0b", "V1b", r["seed"], det)
                    if d is not None:
                        r["truth_events_detected"], r["n_truth_events"] = sum(d), len(d)
        s_ = summ[v] = ev.summarize(rows, ep)
        pu, d_, c_ = s_["precision_unique"], s_["detection"], s_["conditional_on_detection"]
        print(f"| {v} | {s_['correct']['k']} | {s_['rank1_accuracy']['k']} | {s_['answer_rate']['k']} | "
              f"{pu['k']}/{pu['n']} [{100 * pu['ci95'][0]:.0f}, {100 * pu['ci95'][1]:.0f}] | "
              f"{s_['confident_wrong']['k']} | {s_['timeout']['k']} | {d_['detected']}/{d_['truth_events']} | "
              f"{s_['detection_failures']['k']} | {c_['n']} | {c_['rank1_accuracy']['k']} | "
              f"{c_['precision_unique']['k']}/{c_['precision_unique']['n']} |")
    # conditional accuracy on the runs fully detected under both the reference and the arm
    both = [s for s in SEEDS if all(ev.detection_complete(by[(v, s)]) for v in ("V1", "V1b") if (v, s) in by)]
    print(f"\nRuns fully detected under both V1 and V1b: {len(both)}; rank-1 acc there: V1 "
          f"{sum(r1['V1'][s] for s in both)}, V1b {sum(r1['V1b'][s] for s in both)}")
    print("\nPaired (arm vs reference):")
    for va, vr in (("V1b", "V1"), ("V0b", "V0")):
        t = ev.paired_sign(r1[va], r1[vr])
        cw = ev.paired_sign([by[(va, s)].get("outcome") == "confident_wrong" for s in SEEDS],
                            [by[(vr, s)].get("outcome") == "confident_wrong" for s in SEEDS])
        w = ev.paired_wilcoxon([by[(va, s)].get("rank1_error_m") for s in SEEDS],
                               [by[(vr, s)].get("rank1_error_m") for s in SEEDS])
        g = ev.paired_wilcoxon([by[(va, s)].get("true_assignment_group_rank") for s in SEEDS],
                               [by[(vr, s)].get("true_assignment_group_rank") for s in SEEDS])
        print(f"- {va} vs {vr}: rank-1 acc {t['a_only']} / {t['b_only']} (p {t['p']:.3g}); confident-wrong "
              f"{cw['a_only']} / {cw['b_only']} (p {cw['p']:.3g}); rank-1 error median diff {w['median_diff']:+.0f} m "
              f"(n {w['n']}, p {w['p']:.3g}); true-assignment rank median diff {g['median_diff']:+.1f} (n {g['n']}, "
              f"p {g['p']:.3g})")
    changed = [s for s in SEEDS if r1["V1b"][s] != r1["V1"][s]]
    print(f"\nSeeds whose V1 rank-1 result changed (hand-check at least 5): {changed}")

    if not a.no_snr:
        snr = snr_by_event(SEEDS, by, a.jobs)
        print("\n## Recall against peak band SNR (dB), all-on seeds 0-99: detected / events")
        print("| arm | " + " | ".join(SNR_LABELS) + " | spurious / clip-minute |")
        print("|---|" + "---|" * (len(SNR_LABELS) + 1))
        for label, vs, sp in (("reference (V0/V1)", ("V0", "V1"), sp_ref), ("arm b (V0b/V1b)", ("V0b", "V1b"), sp_arm)):
            num, den = [0] * len(SNR_LABELS), [0] * len(SNR_LABELS)
            for s in SEEDS:
                d = event_detection(by, *vs, s, det) or []
                for i, x in enumerate(d):
                    v = snr.get((s, i))
                    if v is None:
                        continue
                    b = next(k for k in range(len(SNR_LABELS)) if SNR_EDGES[k] <= v < SNR_EDGES[k + 1])
                    den[b] += 1
                    num[b] += x
            print(f"| {label} | " + " | ".join(f"{n}/{d}" if d else "" for n, d in zip(num, den))
                  + f" | {sp / 100:.2f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
