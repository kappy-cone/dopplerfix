"""Blind result commitment: a SHA-256 of the top candidates, fixed before any reference is consulted.

On real recordings the truth must not influence the result. Hashing the result as soon as the search ends,
and recording the hash before anything truth-derived is reported, makes that checkable: anyone holding the
hash can later confirm that the saved result is the one committed. The idea comes from the method
description of an independent tool with the same goal (Espaye/aircraft-doppler, docs/method.md §6); this is a
fresh implementation.

The commitment covers search outputs only (rank, refined location, score, Δt and the assigned aircraft per
event), serialised canonically: JSON with sorted keys and no whitespace, every number as a fixed-format
string. Scores are taken at float32 precision, the precision results.gpkg stores, so a hash recomputed from
the saved candidates layer matches the one computed in memory.
"""
from __future__ import annotations

import hashlib
import json
from typing import Sequence

import numpy as np

VERSION = 1
FIELDS = ("rank", "lat", "lon", "score", "dt_s", "tracks")


def _num(x: float | None, fmt: str) -> str | None:
    if x is None:
        return None
    x = float(x)
    return "nan" if np.isnan(x) else format(x, fmt)


def candidate_record(rank: int, lat: float, lon: float, score: float, dt_s: float, tracks: Sequence[str]) -> dict:
    """One candidate in canonical form (strings for every number, so the hash does not depend on float repr)."""
    return {"rank": int(rank), "lat": _num(lat, ".7f"), "lon": _num(lon, ".7f"),
            "score": _num(np.float32(score), ".9g"), "dt_s": _num(dt_s, ".3f"),
            "tracks": [str(t) for t in tracks]}


def payload(records: Sequence[dict], n_candidates: int, top_k: int) -> dict:
    """What is hashed: the first ``top_k`` canonical records by rank, plus the candidate count."""
    recs = sorted(records, key=lambda r: r["rank"])[:int(top_k)]
    return {"version": VERSION, "top_k": int(top_k), "n_candidates": int(n_candidates), "candidates": list(recs)}


def canonical_bytes(p: dict) -> bytes:
    return json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(p: dict) -> str:
    """SHA-256 hex digest of the canonical serialisation."""
    return hashlib.sha256(canonical_bytes(p)).hexdigest()


def commit(records: Sequence[dict], n_candidates: int, top_k: int) -> dict:
    """run.json ``result_commitment`` block: the digest, the fields it covers, and the hashed payload itself."""
    p = payload(records, n_candidates, top_k)
    return {"sha256": digest(p), "version": VERSION, "fields": list(FIELDS), "top_k": int(top_k), "payload": p}


def verify(records: Sequence[dict], n_candidates: int, recorded: dict, expected_sha256: str | None = None) -> dict:
    """Check saved candidates against a recorded commitment (and, optionally, a hash kept elsewhere).

    ``ok`` is True only when the hash recomputed from ``records`` equals the recorded hash and, if given,
    ``expected_sha256``. ``mismatched_ranks`` lists the ranks whose record differs from the recorded payload
    (when one was recorded), to show what changed.
    """
    if int(recorded.get("version", -1)) != VERSION:
        raise ValueError(f"unsupported commitment version {recorded.get('version')!r} (this code: {VERSION})")
    p = payload(records, n_candidates, int(recorded["top_k"]))
    h = digest(p)
    ok = h == recorded["sha256"] and (expected_sha256 is None or h == expected_sha256.strip().lower())
    out = {"ok": bool(ok), "recomputed_sha256": h, "recorded_sha256": recorded["sha256"],
           "expected_sha256": expected_sha256, "mismatched_ranks": None}
    rp = recorded.get("payload")
    if rp is not None:
        got = {r["rank"]: r for r in p["candidates"]}
        want = {r["rank"]: r for r in rp.get("candidates", [])}
        out["mismatched_ranks"] = sorted(k for k in set(got) | set(want) if got.get(k) != want.get(k))
        out["n_candidates_matches"] = rp.get("n_candidates") == p["n_candidates"]
    return out
