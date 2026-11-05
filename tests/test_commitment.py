"""Blind result commitment (doppler/commitment.py, export.verify_result): reproducible hash, tamper detection."""
import json

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point

from doppler import commitment as cm
from doppler import export


def _records(n=6, seed=0):
    rng = np.random.default_rng(seed)
    return [cm.candidate_record(k + 1, 40.0 + rng.normal(0, 0.01), -105.2 + rng.normal(0, 0.01),
                                1.0 + k * 0.137 + rng.uniform(0, 1e-3), rng.normal(0, 2), ["a1b2c3", "" if k % 2 else "d4e5f6"])
            for k in range(n)]


def test_hash_is_reproducible_and_independent_of_record_order():
    recs = _records()
    a = cm.commit(recs, 6, 5)
    b = cm.commit(list(reversed(recs)), 6, 5)
    assert a["sha256"] == b["sha256"] == cm.digest(a["payload"])
    assert len(a["sha256"]) == 64 and a["fields"] == list(cm.FIELDS) and a["top_k"] == 5
    assert [r["rank"] for r in a["payload"]["candidates"]] == [1, 2, 3, 4, 5]
    # the canonical form is fixed: sorted keys, no whitespace, numbers as fixed-format strings
    text = cm.canonical_bytes(a["payload"]).decode()
    assert " " not in text and text.startswith('{"candidates":[{"dt_s":')
    # a round trip through JSON (as run.json stores it) hashes the same
    assert cm.digest(json.loads(json.dumps(a["payload"]))) == a["sha256"]


def test_hash_changes_with_any_committed_field():
    base = cm.commit(_records(), 6, 5)["sha256"]
    for field, value in (("lat", "40.0000001"), ("score", "9"), ("dt_s", "0.001"), ("tracks", ["zzzzzz", ""])):
        recs = _records()
        recs[2] = {**recs[2], field: value}
        assert cm.commit(recs, 6, 5)["sha256"] != base, field
    assert cm.commit(_records(), 7, 5)["sha256"] != base           # candidate count is committed
    assert cm.commit(_records(), 6, 4)["sha256"] != base           # so is top_k
    recs = _records()
    recs[5] = {**recs[5], "score": "9"}                            # rank 6 is outside the top 5
    assert cm.commit(recs, 6, 5)["sha256"] == base


def test_score_is_committed_at_the_float32_precision_results_gpkg_stores():
    x = 1.234567891234
    assert cm.candidate_record(1, 0, 0, x, 0, [])["score"] == cm.candidate_record(1, 0, 0, float(np.float32(x)), 0, [])["score"]
    assert cm.candidate_record(1, 40.123456789, -105.0, 1.0, -0.0004, [])["lat"] == "40.1234568"


def test_verify_fails_on_a_tampered_result_and_a_wrong_external_hash():
    recs = _records()
    rec = cm.commit(recs, 6, 5)
    assert cm.verify(recs, 6, rec)["ok"]
    assert cm.verify(recs, 6, rec, rec["sha256"].upper())["ok"]
    bad = [dict(r) for r in recs]
    bad[0]["tracks"] = ["ffffff", ""]
    v = cm.verify(bad, 6, rec)
    assert not v["ok"] and v["mismatched_ranks"] == [1]
    assert not cm.verify(recs, 6, rec, "0" * 64)["ok"]
    # a run.json edited consistently (payload and hash) still fails against a hash kept elsewhere
    forged = cm.commit(bad, 6, 5)
    assert cm.verify(bad, 6, forged)["ok"] and not cm.verify(bad, 6, forged, rec["sha256"])["ok"]
    with pytest.raises(ValueError):
        cm.verify(recs, 6, {**rec, "version": 99})


def _write_run(run_dir, n=4):
    """A minimal run: candidates layer as write_outputs writes it (score float32) plus run.json's commitment."""
    rng = np.random.default_rng(1)
    lat, lon = 40.0 + rng.normal(0, 0.01, n), -105.2 + rng.normal(0, 0.01, n)
    score = 1.0 + np.sort(rng.uniform(0, 1, n))
    dt = list(rng.normal(0, 2, n))
    tracks = [["a1b2c3", "d4e5f6"] if k else ["a1b2c3", ""] for k in range(n)]
    cand = {"cell_id": list(range(n)), "rank": np.arange(1, n + 1, dtype=np.int32), "score": score.astype(np.float32),
            "dt_s": dt, "est_lat": list(lat), "est_lon": list(lon),
            "track_e0": [t[0] for t in tracks], "track_e1": [t[1] for t in tracks]}
    run_dir.mkdir()
    gpd.GeoDataFrame(cand, geometry=[Point(o, a) for a, o in zip(lat, lon)], crs="EPSG:4326").to_file(
        run_dir / "results.gpkg", layer="candidates", driver="GPKG")
    recs = [cm.candidate_record(k + 1, lat[k], lon[k], score[k], dt[k], tracks[k]) for k in range(n)]  # float64 in memory
    rc = cm.commit(recs, n, 3)
    (run_dir / "run.json").write_text(json.dumps({"result_commitment": rc}))
    return rc


def test_verify_result_recomputes_the_hash_from_results_gpkg(tmp_path):
    rc = _write_run(tmp_path / "run")
    v = export.verify_result(tmp_path / "run")
    assert v["ok"] and v["recomputed_sha256"] == rc["sha256"] and v["mismatched_ranks"] == []
    assert export.verify_result(tmp_path / "run", rc["sha256"])["ok"]
    assert not export.verify_result(tmp_path / "run", "ab" * 32)["ok"]


def test_verify_result_detects_an_edited_candidates_layer(tmp_path):
    _write_run(tmp_path / "run")
    path = tmp_path / "run" / "results.gpkg"
    df = gpd.read_file(path, layer="candidates")
    df.loc[df["rank"] == 2, "est_lat"] += 1e-4                      # ~11 m
    df.to_file(path, layer="candidates", driver="GPKG")
    v = export.verify_result(tmp_path / "run")
    assert not v["ok"] and v["mismatched_ranks"] == [2]


def test_verify_result_needs_a_recorded_commitment(tmp_path):
    _write_run(tmp_path / "run")
    (tmp_path / "run" / "run.json").write_text("{}")
    with pytest.raises(KeyError):
        export.verify_result(tmp_path / "run")
