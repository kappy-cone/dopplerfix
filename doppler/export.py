"""Writers: run directories, results.gpkg, score.tif (COG), run.json, Parquet tables, synthetic scenarios.

Storage rules (CLAUDE.md): only stage survivors are written; the full grid layer
only for regional runs or on request; rasters are compressed tiled COGs cropped
to the survivors; tables are zstd Parquet with only the columns used.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
import soundfile as sf
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point

from .aircraft_types import CLASS_NAMES
from .fit import EventFit
from .synth import Scenario
from .tracks import tracks_to_frame

RESULTS_LAYERS = ("grid", "stage_cpa", "stage_offset", "stage_silence", "candidates", "tracks", "event_cpa_points")
SCENARIO_TRACK_COLUMNS = ["time_utc", "icao24", "callsign", "lat", "lon", "geometric_alt_m", "baro_alt_m",
                          "ground_speed_mps", "track_deg", "vertical_rate_mps"]


def _iso(t_s: float) -> str:
    return pd.Timestamp(t_s, unit="s", tz="UTC").isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Parquet (fastparquet API directly: pandas deprecates its fastparquet engine)
# ---------------------------------------------------------------------------
def write_parquet(df: pd.DataFrame, path: Path) -> Path:
    import fastparquet

    fastparquet.write(str(path), df.reset_index(drop=True), compression="ZSTD", write_index=False)
    return Path(path)


def read_parquet(path: Path, columns: list[str] | None = None) -> pd.DataFrame:
    import fastparquet

    pf = fastparquet.ParquetFile(str(path))
    cols = None if columns is None else [c for c in columns if c in pf.columns]
    return pf.to_pandas(columns=cols)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def event_record(e: EventFit, start_time_s: float | None = None) -> dict:
    """JSON-friendly summary of one fitted event (ridge points included for scoring)."""
    rec = {
        "window_s": [e.window.start_s, e.window.end_s],
        "band_hz": list(e.window.band_hz),
        "f0_hz": e.f0_hz, "v_mps": e.v_mps, "d_m": e.d_m,
        "t0_rel_s": e.t0_s, "t_cpa_received_rel_s": e.t_cpa_received_s,
        "f_pre_hz": e.f_pre_hz, "f_post_hz": e.f_post_hz,
        "drift_per_s": e.drift_per_s, "sigma": e.sigma,
        "rms_hz": e.rms_hz, "noise_hz": e.noise_hz, "n_points": e.n_points,
        "subharmonic": e.ridge.subharmonic, "converged": e.converged,
        "class": CLASS_NAMES[e.cls], "coverage_tau": [float(x) for x in e.coverage],
        "class_features": None if e.class_features is None else {
            "ridge_band": e.class_features.ridge_band, "harmonics": int(e.class_features.harmonics),
            "broadband_rise_db": float(e.class_features.broadband_rise_db)},
        "ridge": {"t_rel_s": e.ridge.t_s.round(4).tolist(), "f_hz": e.ridge.f_hz.round(4).tolist(),
                  "excess_db": e.ridge.excess_db.round(2).tolist()},
    }
    if start_time_s is not None:
        rec["t0_utc"] = _iso(start_time_s + e.t0_s)
    return rec


def write_events(fits: list[EventFit], path: Path, start_time_s: float | None = None) -> None:
    """Events as JSON (``.json``) or as two zstd Parquet tables (``X.parquet`` + ``X_ridges.parquet``)."""
    path = Path(path)
    if path.suffix != ".parquet":
        path.write_text(json.dumps([event_record(e, start_time_s) for e in fits], indent=1))
        return
    rows, ridges = [], []
    for k, e in enumerate(fits):
        r = event_record(e, start_time_s)
        rows.append({"event": k, "class": r["class"], "t_cpa_received_rel_s": e.t_cpa_received_s,
                     "f0_hz": e.f0_hz, "v_mps": e.v_mps, "d_m": e.d_m, "t0_rel_s": e.t0_s,
                     **{f"sigma_{n}": float(v) for n, v in e.sigma.items()},
                     "coverage_pre": r["coverage_tau"][0], "coverage_post": r["coverage_tau"][1],
                     "rms_hz": e.rms_hz, "noise_hz": e.noise_hz, "n_points": e.n_points,
                     "window_start_s": e.window.start_s, "window_end_s": e.window.end_s,
                     "subharmonic": e.ridge.subharmonic, "t0_utc": r.get("t0_utc", ""),
                     # everything read_events needs to rebuild the EventFit exactly (the events seam, P0.3)
                     "cov_json": json.dumps(np.asarray(e.cov, float).tolist()),
                     "sigma_json": json.dumps({n: float(v) for n, v in e.sigma.items()}),
                     "drift_per_s": e.drift_per_s, "converged": bool(e.converged), "c_mps": e.c,
                     "window_band_lo_hz": e.window.band_hz[0], "window_band_hi_hz": e.window.band_hz[1],
                     "window_peak_s": e.window.peak_s, "window_peak_excess_db": e.window.peak_excess_db,
                     "window_manual": bool(e.window.manual), "ridge_noise_hz": e.ridge.noise_hz,
                     "ridge_band_lo_hz": e.ridge.band_hz[0], "ridge_band_hi_hz": e.ridge.band_hz[1],
                     "ridge_subharmonic_excess_db": e.ridge.subharmonic_excess_db,
                     "class_features_json": json.dumps(r["class_features"])})
        ridges.append(pd.DataFrame({"event": np.full(len(e.ridge.t_s), k, np.int16),
                                    "t_rel_s": e.ridge.t_s.astype(np.float64),
                                    "f_hz": e.ridge.f_hz.astype(np.float64),
                                    "excess_db": e.ridge.excess_db.astype(np.float64)}))
    write_parquet(pd.DataFrame(rows), path)
    write_parquet(pd.concat(ridges, ignore_index=True) if ridges else pd.DataFrame(
        {"event": [], "t_rel_s": [], "f_hz": [], "excess_db": []}), path.with_name(path.stem + "_ridges.parquet"))


def read_events(path: Path) -> list[EventFit]:
    """Rebuild the EventFits written by :func:`write_events` (Parquet form), bit-exact for the pipeline."""
    from .aircraft_types import class_code
    from .classify import ClassFeatures
    from .detect import EventWindow
    from .ridge import Ridge

    path = Path(path)
    ev = read_parquet(path)
    rd = read_parquet(path.with_name(path.stem + "_ridges.parquet"))
    out = []
    for _, r in ev.sort_values("event").iterrows():
        pts = rd[rd["event"] == r["event"]]
        ridge = Ridge(pts["t_rel_s"].to_numpy(float), pts["f_hz"].to_numpy(float), pts["excess_db"].to_numpy(float),
                      float(r["ridge_noise_hz"]), (float(r["ridge_band_lo_hz"]), float(r["ridge_band_hi_hz"])),
                      int(r["subharmonic"]), float(r["ridge_subharmonic_excess_db"]))
        win = EventWindow(float(r["window_start_s"]), float(r["window_end_s"]),
                          (float(r["window_band_lo_hz"]), float(r["window_band_hi_hz"])), float(r["window_peak_s"]),
                          float(r["window_peak_excess_db"]), bool(r["window_manual"]))
        cf = json.loads(r["class_features_json"]) if r.get("class_features_json") else None
        cls = class_code(str(r["class"]))
        e = EventFit(window=win, ridge=ridge, f0_hz=float(r["f0_hz"]), v_mps=float(r["v_mps"]), d_m=float(r["d_m"]),
                     t0_s=float(r["t0_rel_s"]), t_cpa_received_s=float(r["t_cpa_received_rel_s"]),
                     sigma=json.loads(r["sigma_json"]), cov=np.asarray(json.loads(r["cov_json"]), float),
                     rms_hz=float(r["rms_hz"]), n_points=int(r["n_points"]), c=float(r["c_mps"]),
                     converged=bool(r["converged"]), drift_per_s=float(r["drift_per_s"]), cls=cls,
                     class_features=None if cf is None else ClassFeatures(cf["ridge_band"], cf["harmonics"],
                                                                           cf["broadband_rise_db"], cls),
                     noise_hz=float(r["noise_hz"]))
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# Synthetic scenarios
# ---------------------------------------------------------------------------
def write_scenario(sc: Scenario, out_dir: Path) -> dict[str, Path]:
    """audio.flac (24-bit) + audio.json (time prior) + tracks.parquet + aircraft_db.csv + truth.json + truth.gpkg."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {k: out / v for k, v in {
        "audio": "audio.flac", "sidecar": "audio.json", "tracks": "tracks.parquet",
        "truth": "truth.json", "gpkg": "truth.gpkg", "registry": "aircraft_db.csv"}.items()}

    sf.write(paths["audio"], sc.audio, sc.fs_hz, subtype="PCM_24", format="FLAC")
    stated = sc.stated_start_s if sc.stated_start_s is not None else sc.start_time_s
    paths["sidecar"].write_text(json.dumps({"mode": "known", "start_utc": _iso(stated)}, indent=1))
    df = (sc.adsb_frame if sc.adsb_frame is not None else tracks_to_frame(sc.tracks, sc.frame))[SCENARIO_TRACK_COLUMNS]
    write_parquet(df, paths["tracks"])
    if sc.registry is not None:
        sc.registry.to_csv(paths["registry"], index=False)

    truth = {
        "mic": {"lat": sc.mic_lat, "lon": sc.mic_lon, "ground_elev_m": sc.ground_elev_m,
                "height_agl_m": sc.params.mic_height_m, "enu_m": sc.mic_enu.tolist()},
        "enu_origin": asdict(sc.frame),
        "recording_start_utc": _iso(sc.start_time_s),
        "speed_of_sound_mps": sc.c,
        "events": [{**asdict(t), "t0_utc": _iso(sc.start_time_s + t.t0_rel_s), "callsign": s.callsign,
                    "turn_rate_deg_s": s.turn_rate_deg_s, "heading_deg": s.heading_deg, "type_code": s.type_code}
                   for t, s in zip(sc.truth, sc.event_specs)],
        "non_events": [asdict(d) for d in sc.decoys],
        "roles": {s.icao24: s.role for s in sc.specs},
        "synth_params": asdict(sc.params),
        "realism": None if sc.realism is None else {
            "params": asdict(sc.realism), "c_true_mps": sc.c_true, "c_pipeline_mps": sc.c,
            "wind_enu_mps": list(sc.wind_enu), "stated_start_utc": _iso(stated),
            "clock_error_s": stated - sc.start_time_s, "mic_height_true_m": float(sc.mic_enu[2] + sc.frame.h0
                                                                                - sc.ground_elev_m),
            "adsb": sc.adsb_info},
        "aircraft": sc.aircraft_info,
    }
    paths["truth"].write_text(json.dumps(truth, indent=1, default=float))

    crs = "EPSG:4326"
    gpd.GeoDataFrame({"name": ["hidden_mic"]}, geometry=[Point(sc.mic_lon, sc.mic_lat)], crs=crs) \
        .to_file(paths["gpkg"], layer="mic_truth", driver="GPKG")
    roles = {s.icao24: s.role for s in sc.specs}
    gpd.GeoDataFrame(
        {"icao24": [t.icao24 for t in sc.tracks], "callsign": [t.callsign for t in sc.tracks],
         "role": [roles[t.icao24] for t in sc.tracks]},
        geometry=[LineString(np.c_[t.lon_deg, t.lat_deg]) for t in sc.tracks], crs=crs,
    ).to_file(paths["gpkg"], layer="tracks", driver="GPKG")
    cpa = []
    ev_tracks = [t for t in sc.tracks if roles[t.icao24] == "event"]
    for tr, tru in zip(ev_tracks, sc.truth):
        i = int(np.argmin(np.abs(tr.t_s - (sc.start_time_s + tru.t0_rel_s))))
        cpa.append(Point(tr.lon_deg[i], tr.lat_deg[i]))
    gpd.GeoDataFrame({"icao24": [t.icao24 for t in sc.truth], "d_m": [t.d_m for t in sc.truth],
                      "t0_utc": [_iso(sc.start_time_s + t.t0_rel_s) for t in sc.truth]},
                     geometry=cpa, crs=crs).to_file(paths["gpkg"], layer="cpa_points", driver="GPKG")
    return paths


# ---------------------------------------------------------------------------
# Run directories
# ---------------------------------------------------------------------------
def new_run_dir(root: Path, run_id: str | None = None, tag: str = "") -> Path:
    """outputs/<run_id>/ with run_id = UTC timestamp (+ tag), unique."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if run_id is None:
        run_id = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ") + (f"_{tag}" if tag else "")
    d = root / run_id
    k = 1
    while d.exists():
        d = root / f"{run_id}_{k}"
        k += 1
    d.mkdir()
    return d


def update_latest(root: Path, run_dir: Path) -> Path:
    """Atomically point outputs/latest at run_dir (relative symlink, never a copy)."""
    root = Path(root)
    link = root / "latest"
    tmp = root / ".latest.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(Path(run_dir).name, tmp)
    os.replace(tmp, link)
    return link


def dir_footprint(d: Path) -> dict:
    files = {p.relative_to(d).as_posix(): p.stat().st_size for p in sorted(Path(d).rglob("*"))
             if p.is_file() and not p.is_symlink()}
    return {"total_bytes": int(sum(files.values())), "files": files}


def list_runs(root: Path) -> list[Path]:
    """Run directories (those containing run.json), oldest first."""
    root = Path(root)
    runs = [p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and (p / "run.json").exists()]
    return sorted(runs, key=lambda p: (p.stat().st_mtime, p.name))


def prune_runs(root: Path, keep: int) -> tuple[list[Path], int]:
    """Delete all but the newest ``keep`` runs (never the latest symlink's target). Returns (removed, bytes freed)."""
    root = Path(root)
    runs = list_runs(root)
    latest = (root / "latest").resolve() if (root / "latest").is_symlink() else None
    victims = [r for r in runs[:max(0, len(runs) - keep)] if r.resolve() != latest]
    freed = 0
    for r in victims:
        freed += dir_footprint(r)["total_bytes"]
        shutil.rmtree(r)
    link = root / "latest"
    if link.is_symlink() and not link.exists():
        link.unlink()
    return victims, freed


def write_run_json(run_dir: Path, info: dict) -> Path:
    """run.json with the run's disk footprint (all files except run.json itself, which is listed separately)."""
    path = Path(run_dir) / "run.json"
    fp = dir_footprint(run_dir)
    fp["files"].pop("run.json", None)
    fp["total_bytes"] = int(sum(fp["files"].values()))
    info = {**info, "disk_footprint": fp}
    text = json.dumps(info, indent=1, default=_json_default)
    path.write_text(text)
    info["disk_footprint"]["run_json_bytes"] = path.stat().st_size
    path.write_text(json.dumps(info, indent=1, default=_json_default))
    return path


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)


# ---------------------------------------------------------------------------
# results.gpkg
# ---------------------------------------------------------------------------
class LayerBuffer:
    """Accumulate GeoDataFrame rows per layer and append them to a GeoPackage in batches.

    Layers that fit in one batch (< flush_rows) get no R-tree: at that size it
    only costs disk (~50 B/feature) and QGIS renders them instantly anyway.
    Larger layers (US-wide stages) are flushed in batches and indexed.
    """

    def __init__(self, path: Path, flush_rows: int = 50_000):
        self.path = Path(path)
        self.flush_rows = flush_rows
        self.buf: dict[str, list[gpd.GeoDataFrame]] = {}
        self.n: dict[str, int] = {}
        self.written: set[str] = set()

    def add(self, layer: str, gdf: gpd.GeoDataFrame) -> None:
        if len(gdf) == 0:
            return
        self.buf.setdefault(layer, []).append(gdf)
        self.n[layer] = self.n.get(layer, 0) + len(gdf)
        if sum(len(g) for g in self.buf[layer]) >= self.flush_rows:
            self._flush(layer)

    def _flush(self, layer: str) -> None:
        parts = self.buf.pop(layer, [])
        if not parts:
            return
        gdf = pd.concat(parts, ignore_index=True)
        big = layer in self.written or len(gdf) >= self.flush_rows
        pyogrio.write_dataframe(gdf, self.path, layer=layer, driver="GPKG", append=layer in self.written,
                                layer_options={"SPATIAL_INDEX": "YES" if big else "NO"})
        self.written.add(layer)

    def ensure(self, layer: str, empty: gpd.GeoDataFrame) -> None:
        """Create the layer (empty) if nothing was written to it, so the layer set is fixed."""
        if layer not in self.written and not self.buf.get(layer):
            pyogrio.write_dataframe(empty, self.path, layer=layer, driver="GPKG", append=False,
                                    layer_options={"SPATIAL_INDEX": "NO"})
            self.written.add(layer)

    def close(self) -> dict[str, int]:
        for layer in list(self.buf):
            self._flush(layer)
        if self.path.exists():
            import sqlite3

            con = sqlite3.connect(self.path)
            con.execute("VACUUM")
            con.close()
        return dict(self.n)


# ---------------------------------------------------------------------------
# score.tif
# ---------------------------------------------------------------------------
def write_score_raster(path: Path, i: np.ndarray, j: np.ndarray, value: np.ndarray, spacing_m: float, crs,
                       description: str, max_cells: int = 50_000_000) -> dict:
    """Cell values on the grid's own CRS, cropped to the cells given, NaN nodata elsewhere.

    COG (ZSTD, 256-px tiles) when the cropped raster fits ``max_cells``; otherwise a
    sparse tiled ZSTD GeoTIFF written block by block (US-wide scatter), which is
    recorded in the returned info.
    """
    path = Path(path)
    if len(i) == 0:
        return {"written": False, "reason": "no surviving cells"}
    i0, i1, j0, j1 = int(i.min()), int(i.max()), int(j.min()), int(j.max())
    W, H = i1 - i0 + 1, j1 - j0 + 1
    transform = from_origin(i0 * spacing_m, (j1 + 1) * spacing_m, spacing_m, spacing_m)
    rows, cols = j1 - j, i - i0
    prof = dict(width=W, height=H, count=1, dtype="float32", crs=crs, transform=transform, nodata=np.nan,
                compress="ZSTD")
    if W * H <= max_cells:
        arr = np.full((H, W), np.nan, np.float32)
        arr[rows, cols] = value.astype(np.float32)
        with rasterio.open(path, "w", driver="COG", blocksize=256, overview_resampling="nearest", **prof) as ds:
            ds.write(arr, 1)
            ds.set_band_description(1, description)
        kind = "COG"
    else:
        bs = 256
        with rasterio.open(path, "w", driver="GTiff", tiled=True, blockxsize=bs, blockysize=bs, SPARSE_OK=True,
                           **prof) as ds:
            br, bc = rows // bs, cols // bs
            key = br * ((W + bs - 1) // bs) + bc
            for k in np.unique(key):
                m = key == k
                r0, c0 = int(br[m][0]) * bs, int(bc[m][0]) * bs
                h, w = min(bs, H - r0), min(bs, W - c0)
                blk = np.full((h, w), np.nan, np.float32)
                blk[rows[m] - r0, cols[m] - c0] = value[m]
                ds.write(blk, 1, window=rasterio.windows.Window(c0, r0, w, h))
            ds.set_band_description(1, description)
        kind = "sparse GTiff (raster too large for an in-memory COG)"
    return {"written": True, "format": kind, "width": W, "height": H, "crs": str(crs)}


# ---------------------------------------------------------------------------
# Result commitment: re-check a saved run (doppler/commitment.py holds the canonical form)
# ---------------------------------------------------------------------------
def commitment_records_from_gpkg(path: Path) -> tuple[list[dict], int]:
    """Canonical commitment records rebuilt from a results.gpkg candidates layer, and the candidate count."""
    from .commitment import candidate_record

    df = pyogrio.read_dataframe(path, layer="candidates", read_geometry=False)
    n_ev = sum(1 for c in df.columns if c.startswith("track_e"))
    recs = [candidate_record(int(r["rank"]), r["est_lat"], r["est_lon"], r["score"], r["dt_s"],
                             ["" if r[f"track_e{e}"] is None else str(r[f"track_e{e}"]) for e in range(n_ev)])
            for _, r in df.iterrows()]
    return recs, len(df)


def verify_result(run_dir: Path, expected_sha256: str | None = None) -> dict:
    """Recompute a run's result commitment from its results.gpkg and compare it with run.json (and a hash kept
    elsewhere, if given). Raises KeyError when the run recorded no commitment."""
    from .commitment import verify

    run_dir = Path(run_dir)
    rj = json.loads((run_dir / "run.json").read_text())
    if "result_commitment" not in rj:
        raise KeyError(f"{run_dir / 'run.json'} has no result_commitment (run locate with --commit-result)")
    recs, n = commitment_records_from_gpkg(run_dir / "results.gpkg")
    return verify(recs, n, rj["result_commitment"], expected_sha256)
