"""Writers: synthetic scenarios, event tables, and (milestone 6) QGIS rasters/vectors."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import soundfile as sf
from shapely.geometry import LineString, Point

from .aircraft_types import CLASS_NAMES
from .fit import EventFit
from .geo import LocalFrame
from .synth import Scenario
from .tracks import tracks_to_frame


def _iso(t_s: float) -> str:
    return pd.Timestamp(t_s, unit="s", tz="UTC").isoformat().replace("+00:00", "Z")


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
    Path(path).write_text(json.dumps([event_record(e, start_time_s) for e in fits], indent=1))


def write_scenario(sc: Scenario, out_dir: Path) -> dict[str, Path]:
    """audio.wav + audio.json (time-prior sidecar) + tracks.csv + truth.json + truth.gpkg."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {k: out / v for k, v in {
        "audio": "audio.wav", "sidecar": "audio.json", "tracks": "tracks.csv",
        "truth": "truth.json", "gpkg": "truth.gpkg", "registry": "aircraft_db.csv"}.items()}

    sf.write(paths["audio"], sc.audio, sc.fs_hz, subtype="FLOAT")
    paths["sidecar"].write_text(json.dumps({"mode": "known", "start_utc": _iso(sc.start_time_s)}, indent=1))
    df = tracks_to_frame(sc.tracks, sc.frame)
    df["time_utc"] = df["time_utc"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    df.to_csv(paths["tracks"], index=False, float_format="%.7f")
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
