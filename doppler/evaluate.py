"""Validation metrics, outcome tiers and the failure taxonomy (reports/combined_plan.md §3.0, P0.4, U10).

Pure functions over plain numbers and arrays: the ablation driver (cli.py ablate) and run.json's truth
section both use them. Nothing here reads files or feeds back into a run (read-only diagnostics).

Outcome tiers (user decision U10, session 4). The headline estimate is the rank-1 refined point (the
assignment-level optimum replaces it in Phase 1, P1.2):
  * ``abstained``       the run's status is not "unique" (ambiguous, inconsistent, locus, none);
  * ``correct``         unique, headline within ``success_radius_m``, and (synthetic) the true aircraft
                        assignment at rank 1;
  * ``confident_wrong`` unique and either the headline is beyond ``confident_wrong_m`` or the assignment is wrong
                        while the headline is beyond ``success_radius_m``;
  * ``wrong``           any other unique answer (a near miss).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class EvalParams:
    success_radius_m: float = 200.0      # U10: fixed validation constant (does not follow grid.spacing_m)
    confident_wrong_m: float = 1000.0    # "unique" and farther than this: the worst failure
    match_tol_s: float = 10.0            # a truth event matches the detected event nearest in received CPA time
    hop_ratio_tol: float = 0.015         # harmonic hop: ridge/model ratio within this of a simple ratio ≠ 1

    @classmethod
    def from_dict(cls, d: dict) -> "EvalParams":
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


ABSTAIN = ("ambiguous", "inconsistent", "locus", "none")
STAGES = ("grid", "cpa", "offset", "silence", "scored")


def match_events(t_rx_detected: Sequence[float], t_rx_truth: Sequence[float], tol_s: float) -> list[int | None]:
    """For each truth event, the index of the detected event nearest in received CPA time (None if > tol)."""
    det = np.asarray(t_rx_detected, float)
    out: list[int | None] = []
    for t in t_rx_truth:
        if len(det) == 0:
            out.append(None)
            continue
        k = int(np.argmin(np.abs(det - t)))
        out.append(k if abs(det[k] - t) < tol_s else None)
    return out


def assignment_correct(assigned_icao: Sequence[str | None], match: Sequence[int | None],
                       truth_icao: Sequence[str]) -> bool | None:
    """True if every detected truth event is assigned its true aircraft (spurious detections are ignored).

    ``assigned_icao``: icao24 per detected event ('' or None = unmatched). None if no truth event was detected.
    """
    pairs = [(k, ic) for k, ic in zip(match, truth_icao) if k is not None]
    if not pairs:
        return None
    return all((assigned_icao[k] or None) == ic for k, ic in pairs)


def outcome(status: str, err_m: float | None, assign_ok: bool | None, p: EvalParams) -> str:
    """One of correct / abstained / wrong / confident_wrong (module docstring)."""
    if status in ABSTAIN or err_m is None:
        return "abstained"
    ok_assign = assign_ok is not False
    if err_m <= p.success_radius_m and ok_assign:
        return "correct"
    if err_m > p.confident_wrong_m or (not ok_assign and err_m > p.success_radius_m):
        return "confident_wrong"
    return "wrong"


def failure_class(true_stage: str | None, rank1_assign_ok: bool | None, err_m: float | None,
                  p: EvalParams) -> str | None:
    """Failure taxonomy for re-ordering Phases 1-3 (plan §3.0). None when the rank-1 answer is right.

    survival:<stage>  the true assignment never reached scoring (``true_stage`` = last stage it reached);
    discrimination    a wrong assignment is ranked 1;
    bias              the right assignment is ranked 1 but the headline is beyond success_radius_m.
    """
    if true_stage != "scored":
        return f"survival:{true_stage or 'none'}"
    if rank1_assign_ok is False:
        return "discrimination"
    if err_m is None or err_m > p.success_radius_m:
        return "bias"
    return None


def fit_z(fit_v: float, fit_d: float, fit_t_rx: float, sigma: Sequence[float], tol: Sequence[float],
          true_v: float, true_d: float, true_t_rx: float) -> dict:
    """Per-event fit errors against truth: in σ units (fit covariance) and in tolerance units (cascade)."""
    dv, dd, dt = fit_v - true_v, fit_d - true_d, fit_t_rx - true_t_rx
    s = [max(float(x), 1e-12) for x in sigma]
    tl = [max(float(x), 1e-12) for x in tol]
    return {"dv_mps": dv, "dd_m": dd, "dt_rx_s": dt, "z_v": dv / s[0], "z_d": dd / s[1], "z_t": dt / s[2],
            "tol_v": dv / tl[0], "tol_d": dd / tl[1], "tol_t": dt / tl[2]}


def along_cross(d_east_m: float, d_north_m: float, heading_deg: float) -> tuple[float, float]:
    """Error vector (estimate − truth) split along / across a track heading (degrees clockwise from north)."""
    h = np.radians(heading_deg)
    u = np.array([np.sin(h), np.cos(h)])
    e = np.array([d_east_m, d_north_m])
    return float(e @ u), float(e @ np.array([u[1], -u[0]]))


def harmonic_hops(f_ridge: NDArray, f_model: NDArray, tol: float, max_den: int = 4) -> int:
    """Segments where the ridge sits on another tone family member than the event's own fitted curve.

    A point is "hopped" if f_ridge / f_model is within ``tol`` of a simple ratio p/q ≠ 1 (q ≤ max_den) and
    not within 2·tol of 1. Returns the number of contiguous hopped segments.
    """
    r = np.asarray(f_ridge, float) / np.asarray(f_model, float)
    hop = np.zeros(r.shape, bool)
    for q in range(1, max_den + 1):
        for pn in range(1, 4 * q + 1):
            if pn == q:
                continue
            hop |= np.abs(r / (pn / q) - 1) < tol
    hop &= np.abs(r - 1) >= 2 * tol
    return int(np.sum(np.diff(np.r_[0, hop.astype(int)]) == 1))


def fallback_arm(strict: dict, robust: dict, trigger: str) -> dict:
    """Derived arm at zero compute (HANDOFF's strict-first combination): the strict row unless its trigger
    fires, then the robust row.

    ``trigger``: ``zero_survivors`` (strict left no scored candidate) or ``absolute_fit`` (strict left none,
    or its rank 1 is ``inconsistent``).
    """
    fire = strict.get("n_candidates", 0) == 0
    if trigger == "absolute_fit":
        fire = fire or strict.get("status") == "inconsistent"
    elif trigger != "zero_survivors":
        raise ValueError(f"unknown fallback trigger {trigger!r}")
    row = dict(robust if fire else strict)
    row["fallback_fired"] = bool(fire)
    return row


def clopper_pearson(k: int, n: int, alpha: float = 0.05, two_sided: bool = True) -> tuple[float, float]:
    """Exact binomial confidence interval for k successes in n (two-sided, or one-sided upper/lower)."""
    from scipy.stats import beta

    if n == 0:
        return (float("nan"), float("nan"))
    a = alpha / 2 if two_sided else alpha
    lo = 0.0 if k == 0 else float(beta.ppf(a, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - a, k + 1, n - k))
    return lo, hi


def _rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": k / n if n else float("nan"), "ci95": clopper_pearson(k, n)}


def detection_complete(row: dict) -> bool | None:
    """True if every truth event of the run was detected (None if the row lacks the counts)."""
    nt, nd = row.get("n_truth_events"), row.get("truth_events_detected")
    if nt is None or nd is None:
        return None
    return nd >= nt


def detection_class(row: dict) -> str | None:
    """Plan §8.1: ``detection`` when some truth event was never detected, reported *beside* the pre-registered
    taxonomy (failure_class and reorder_rule are unchanged). It separates "the detector never saw it" from
    "the cascade killed it"."""
    return "detection" if detection_complete(row) is False else None


def rank1_ok(row: dict, p: EvalParams) -> bool:
    """Plan §8.1 rank-1 accuracy ignoring status: the rank-1 headline is within success_radius_m and (where
    known) has the true assignment, whatever the run's status. Same assignment rule as ``outcome``."""
    err = row.get("rank1_error_m")
    return err is not None and err <= p.success_radius_m and row.get("rank1_assignment_correct") is not False


def delta_cost(row: dict) -> float | None:
    """Δcost (truth − rank 1, plan §3.0): the best true-assignment hypothesis's score minus the rank-1 score.

    Only comparable when both hypotheses leave the same number of events unmatched: ranking is lexicographic
    (unmatched count first), and a cost over fewer events is not on the same scale. None otherwise, or when
    the row lacks the fields (Stage A rows record neither the true assignment's score nor its unmatched count).
    """
    ta, r1 = row.get("true_assignment_score"), row.get("rank1_score")
    if ta is None or r1 is None or row.get("true_assignment_n_unmatched") is None:
        return None
    if row["true_assignment_n_unmatched"] != row.get("rank1_n_unmatched"):
        return None
    return float(ta) - float(r1)


def paired_wilcoxon(a: Sequence[float | None], b: Sequence[float | None]) -> dict:
    """Wilcoxon signed-rank test on paired continuous values (plan §3.0), a − b; pairs with a None dropped.

    Returns n (pairs used), n_dropped, n_nonzero, median(a − b) and the two-sided p (NaN below 1 non-zero
    difference). Zero differences are dropped (``zero_method="wilcox"``).
    """
    from scipy.stats import wilcoxon

    pairs = [(float(x), float(y)) for x, y in zip(a, b) if x is not None and y is not None]
    d = np.array([x - y for x, y in pairs], float)
    out = {"n": len(pairs), "n_dropped": len(a) - len(pairs), "n_nonzero": int(np.sum(d != 0)),
           "median_diff": float(np.median(d)) if len(d) else float("nan"), "p": float("nan")}
    if out["n_nonzero"] >= 1:
        out["p"] = float(wilcoxon(d, zero_method="wilcox", alternative="two-sided").pvalue)
    return out


def paired_sign(a: Sequence[bool], b: Sequence[bool]) -> dict:
    """Exact sign test on paired binary outcomes (McNemar's exact form): discordant pairs only."""
    from scipy.stats import binomtest

    a_only = sum(1 for x, y in zip(a, b) if x and not y)
    b_only = sum(1 for x, y in zip(a, b) if y and not x)
    n_d = a_only + b_only
    return {"n": len(a), "a_only": a_only, "b_only": b_only,
            "p": float(binomtest(a_only, n_d, 0.5).pvalue) if n_d else float("nan")}


def summarize(rows: Sequence[dict], p: EvalParams | None = None) -> dict:
    """Outcome rates (with two-sided 95 % Clopper–Pearson intervals) and the failure taxonomy over rows, plus
    the plan §8.1 measures: answer rate, rank-1 accuracy ignoring status, detection recall, the ``detection``
    class beside the taxonomy, and outcome tiers conditional on every truth event being detected."""
    p = p or EvalParams()
    out = _summarize_core(rows)
    n = len(rows)
    out["answer_rate"] = _rate(sum(1 for r in rows if r.get("status") == "unique"), n)
    out["rank1_accuracy"] = _rate(sum(1 for r in rows if rank1_ok(r, p)), n)
    known = [r for r in rows if detection_complete(r) is not None]
    nt = sum(r["n_truth_events"] for r in known)
    out["detection"] = {"runs": len(known), "truth_events": nt,
                        "detected": sum(min(r["truth_events_detected"], r["n_truth_events"]) for r in known),
                        "runs_complete": sum(1 for r in known if detection_complete(r))}
    out["detection"]["recall"] = out["detection"]["detected"] / nt if nt else float("nan")
    cross: dict[str, int] = {}
    for r in rows:
        if detection_class(r):
            key = r.get("failure") or ("none (" + str(r.get("outcome")) + ")")
            cross[key] = cross.get(key, 0) + 1
    out["detection_failures"] = {"k": sum(cross.values()), "by_failure": cross}
    full = [r for r in known if detection_complete(r)]
    cond = _summarize_core(full)
    cond["answer_rate"] = _rate(sum(1 for r in full if r.get("status") == "unique"), len(full))
    cond["rank1_accuracy"] = _rate(sum(1 for r in full if rank1_ok(r, p)), len(full))
    out["conditional_on_detection"] = cond
    return out


def _summarize_core(rows: Sequence[dict]) -> dict:
    n = len(rows)
    out: dict = {"n": n}
    for o in ("correct", "abstained", "wrong", "confident_wrong", "timeout", "error"):
        k = sum(1 for r in rows if r.get("outcome") == o)
        out[o] = {"k": k, "rate": k / n if n else float("nan"), "ci95": clopper_pearson(k, n)}
    uniq = [r for r in rows if r.get("status") == "unique"]
    k = sum(1 for r in uniq if r.get("outcome") == "correct")
    out["precision_unique"] = {"k": k, "n": len(uniq), "rate": k / len(uniq) if uniq else float("nan"),
                               "ci95": clopper_pearson(k, len(uniq))}
    fails: dict[str, int] = {}
    for r in rows:
        f = r.get("failure")
        if f:
            fails[f] = fails.get(f, 0) + 1
    out["failures"] = fails
    errs = np.array([r["rank1_error_m"] for r in rows if r.get("rank1_error_m") is not None], float)
    out["rank1_error_m_median"] = float(np.median(errs)) if len(errs) else None
    return out


def reorder_rule(rows: Sequence[dict], frac: float = 0.4) -> str:
    """Plan §3.0 decision rule: which phase goes first, from the all-on failure mix (fixed before any run)."""
    fails = [r.get("failure") for r in rows if r.get("failure")]
    if not fails:
        return "written order (no failures)"
    n = len(fails)
    surv_cpa = sum(1 for f in fails if f in ("survival:grid", "survival:none")) / n   # never passed CPA
    disc = sum(1 for f in fails if f == "discrimination") / n
    bias = sum(1 for f in fails if f == "bias") / n
    if surv_cpa >= frac:
        return "P3.5 before Phase 2 (survival at CPA dominates)"
    if disc >= frac:
        return "Phase 2 keeps its place (discrimination dominates)"
    if bias >= frac:
        return "P1.3 and P3.3 first (bias dominates)"
    return "written order (no class reaches the threshold)"
