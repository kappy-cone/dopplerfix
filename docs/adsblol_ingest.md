# adsb.lol ingest: historical traces onto the track schema

adsb.lol becomes the preferred source of real flight tracks. OpenSky stays in use for its historical datasets
(`docs/opensky_ingest.md`). This note covers what adsb.lol publishes, how it maps onto the track schema, and what
is still unverified. Code: `doppler/adsblol.py` (parsing, pure), `export.iter_archive_json` (streaming),
`cli.py adsblol`, settings in `config.yaml` → `adsblol:`. Tests: `tests/test_adsblol.py`, on hand-made traces.

**No archive has been downloaded.** Everything below comes from the published documentation, read
2026-11-05. Check §4 on one real day before relying on it, and download only with the user's approval (a day
is several GiB).

## 1. What adsb.lol offers
- **Historical archive** (GitHub repos `adsblol/globe_history_<year>`, release assets):
  - one release per day, tag `v<YYYY>.<MM>.<DD>-planes-readsb-prod-0`, split into tar parts (`.tar.aa`, `.tar.ab`, ...);
  - `staging` and `mlatonly` variants also exist. The README says to use prod, and staging when prod is down;
  - prod is about 2.9–4.0 GiB per day in the 2026 listing;
  - each day is the whole world. There is no per-region or per-aircraft download;
  - the contents are readsb trace files: gzipped JSON, one per aircraft.
- **Licence:** the data is under the Open Database License (ODbL 1.0). The repo also carries a CC0 file, and
  the pages don't say which parts that covers. ODbL requires attribution, and share-alike for a derived
  *database* we publish. Read both licence files before publishing any derived track tables. Run outputs that
  only use the data (locations, scores) are a "produced work", which needs attribution only.
- **Live API** (`api.adsb.lol`, compatible with the ADSBExchange RapidAPI): current state only, nothing
  historical. Rate limits are "dynamic", and an API key "will be required" in future. Its docs page did not
  render for a script, so endpoints and limits are unconfirmed. Possible use: logging traffic live during a
  field-recording session.

## 2. Trace format (readsb README-json.md, "trace jsons")
File: `{"icao", "timestamp", "trace": [...], "r", "t", ...}`. The absolute time of a point is
`timestamp + trace[i][0]`. `r` (registration) and `t` (type code) come from readsb's aircraft database.

| index | field | → canonical | conversion |
|---|---|---|---|
| 0 | seconds after `timestamp` | `time_utc` (epoch s) | + timestamp |
| 1, 2 | lat, lon | `lat`, `lon` | – |
| 3 | altitude ft, `"ground"` or null | `baro_alt_m` | × 0.3048. Geometric instead when flag 8 is set; `"ground"` → `onground`, both altitudes NaN |
| 4 | ground speed kt | `ground_speed_mps` | × 1852/3600 |
| 5 | track deg (true heading on the ground) | `track_deg` | – |
| 6 | flags: 1 stale, 2 new leg, 4 geometric rate, 8 geometric altitude | `stale`, `new_leg` | – |
| 7 | vertical rate ft/min | `vertical_rate_mps` | × 0.3048/60. Used only when index 11 is null |
| 8 | aircraft object or null | `callsign` from `flight` | stripped, carried forward within the trace |
| 9 | position source (files from 2022) | `source` | – |
| 10 | geometric altitude ft | `geometric_alt_m` | × 0.3048. readsb's notes say it is referenced to the WGS84 ellipsoid |
| 11 | geometric vertical rate ft/min | `vertical_rate_mps` | × 0.3048/60 |

The output keeps `onground`, `source`, `stale` and `new_leg` for diagnosis; `tracks.py` ignores them.

## 3. Pipeline
```bash
python cli.py adsblol data/adsblol/v2026.11.04-planes-readsb-prod-0.tar.a* \
  --region <name> --start 2026-11-04T14:00:00 --end 2026-11-04T14:30:00 --out data/tracks/<clip>.parquet
```
- **One streaming pass, nothing unpacked.** The parts are read in order as one stream, and the tar is read
  member by member. A trace outside the time window is skipped after reading its first and last time,
  without parsing every point. The rest are parsed, filtered and cropped. Only the crop is written. This
  follows the storage rules: no full archive or unpacked folder on disk, and the parts can be deleted after
  extraction.
- **Crop:** the region's bounding box plus `margin_m` (30 km), and the window plus `pad_s` (300 s).
- **Output:** a canonical Parquet file, which `locate --tracks` reads unchanged. There is also a registry CSV
  (`<out>_types.csv`: icao24, typecode, registration), built from the trace files' `t` field. Pass it as
  `--registry`. For a prop/jet class the type code still needs the ICAO Doc 8643 type table
  (`aircraft_types.type_table_path`, `docs/opensky_ingest.md` §5); without it every aircraft is "unknown".
- **Sources kept** (`adsblol.sources`, PROVISIONAL): `adsb_icao`, `adsb_icao_nt`, `adsr_icao`, `tisb_icao`, `mlat`.
  - MLAT is kept because aircraft without ADS-B out are often the GA props the events come from, and the
    silence filter needs them.
  - MLAT positions are coarser (tens to hundreds of metres) and carry barometric altitude only.
  - Anonymous `~` addresses are kept, with class unknown. Stale points are dropped.
  - Which sources to keep for *scoring* is an open decision; the per-fix `source` column makes it measurable.

## 4. To check on the first real day (each one needs the download approved)
1. **Member names and layout** inside the tar (`traces/<xx>/trace_full_<hex>.json`?). The reader only needs
   "trace" in the name and handles gzip by its magic bytes. Also check whether `trace_recent_*` files
   duplicate `trace_full_*`. If they do, filter on `trace_full`.
2. **Cadence.** The README example has points about 11 s apart, and the docs promise no sampling rate (traces
   may be thinned where flight is steady). Measure the gap distribution near the region. Gaps over `tracks.max_gap_s` (20 s) split segments. Coarse
   points also add interpolation error on turns (`docs/opensky_ingest.md` §4.1 has the same concern for 10 s
   data). This is the main risk to scoring accuracy.
3. **Geometric altitude datum.** Compare it with runway elevation on short final, as planned for OpenSky
   (P5.4).
4. **Time to stream one day** (around 3.5 GiB, perhaps hundreds of thousands of aircraft). If it is slow, the
   cheap pre-check could also test the first point's position, or the parse could be parallelised per part.
   Flag that before adding it.
5. **The `mlatonly` variant:** is it a subset of prod, or extra positions?
6. **Licence files:** which parts are CC0, and the exact attribution wording.

## 5. Not built
- A downloader: it needs the user's approval per download, and parts are large.
- A live-API logger for field sessions (§1).
- Switching the default registry to adsb.lol types.
