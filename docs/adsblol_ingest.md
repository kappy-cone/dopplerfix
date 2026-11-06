# adsb.lol ingest: historical traces onto the track schema

adsb.lol becomes the preferred source of real flight tracks. OpenSky stays in use for its historical datasets
(`docs/opensky_ingest.md`). This note covers what adsb.lol publishes, how it maps onto the track schema, and what
is still unverified. Code: `doppler/adsblol.py` (parsing, pure), `export.iter_archive_json` (streaming),
`cli.py adsblol`, settings in `config.yaml` → `adsblol:`. Tests: `tests/test_adsblol.py`, on hand-made traces.

§1–3 come from the published documentation, read 2026-11-05. §4 checks them on one real day (2026-11-04,
prod), streamed once with the user's approval and never stored whole.

## 1. What adsb.lol offers
- **Historical archive** (GitHub repos `adsblol/globe_history_<year>`, release assets):
  - one release per day, tag `v<YYYY>.<MM>.<DD>-planes-readsb-prod-0`, split into tar parts (`.tar.aa`, `.tar.ab`, ...);
  - `staging` and `mlatonly` variants also exist. The README says to download prod or staging for a day,
    and to ignore whichever is much smaller;
  - prod is about 2.9–4.0 GiB per day in the 2026 listing;
  - each day is the whole world. There is no per-region or per-aircraft download;
  - the contents are readsb trace files: gzipped JSON, one per aircraft.
- **Licence:** the database is under the Open Database License (ODbL 1.0). The CC0 file covers what feeders
  contribute: by feeding, they waive their rights to their data (each release's notes say so). ODbL requires
  attribution, and share-alike for a derived *database* we publish (a cropped track table counts). Run outputs
  that only use the data (locations, scores) are a "produced work", which needs attribution only.
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
| 10 | geometric altitude ft | `geometric_alt_m` | × 0.3048. Ellipsoidal (checked in §4). Dropped on MLAT points (`mlat_geometric_alt`) |
| 11 | geometric vertical rate ft/min | `vertical_rate_mps` | × 0.3048/60 |

The output keeps `onground`, `source`, `stale` and `new_leg` for diagnosis; `tracks.py` ignores them.

## 3. Pipeline
```bash
python cli.py adsblol data/adsblol/v2026.11.04-planes-readsb-prod-0.tar.a* \
  --region <name> --start 2026-11-04T14:00:00 --end 2026-11-04T14:30:00 --out data/tracks/<clip>.parquet
```
To skip storing the parts at all, stream them from the release into stdin (`-`):
```bash
U=https://github.com/adsblol/globe_history_2026/releases/download/v2026.11.04-planes-readsb-prod-0/v2026.11.04-planes-readsb-prod-0.tar
(curl -sSfL $U.aa && curl -sSfL $U.ab) | python cli.py adsblol - \
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
  - MLAT positions are coarser (tens to hundreds of metres). Their geometric altitude is dropped by default
    (§4.3), so a mostly-MLAT track falls back to barometric altitude.
  - `adsb_other`, `adsr_other`, `tisb_other` and `tisb_trackfile` (non-ICAO addresses) are not kept. They were
    under 0.3% of the day's points.
  - Anonymous `~` addresses are kept, with class unknown. Stale points are dropped.
  - Which sources to keep for *scoring* is an open decision; the per-fix `source` column makes it measurable.

## 4. Checked on one real day (2026-11-04, prod)
The day was streamed once from the release through a check script (`curl | tar` reader, nothing unpacked),
which kept only the traces passing near Boulder (a 100 MB subset). `cli.py adsblol` then ran on that subset,
by path and by stdin with identical output, and `cli.py tracks` loaded the result. One day and one area: treat
the numbers as a first look, not a calibration.

1. **Layout.** 72,105 trace files, all `./traces/<xx>/trace_full_<hex>.json`, all gzipped; no
   `trace_recent_*`. The other members (`README.txt`, `LICENSE-ODbL.txt`, `acas/`, `heatmap/*.bin.ttf`) are
   skipped by the "trace" filter. Every point has all 14 fields; no file failed to decode.
2. **Cadence: much finer than the README example.** Near the region, gaps between consecutive airborne points
   of one aircraft: median 2.3 s, 90% under 9.5 s, 99% under 19.9 s. 0.4% of gaps exceed `tracks.max_gap_s`
   (20 s); for aircraft under about 3 km above ground they hold 5.7% of flight time, and those spans become
   segment breaks rather than interpolation. Median gap by source: ADS-B 2.1 s, ADS-R 2.2 s, TIS-B 4.6 s,
   MLAT 5.1 s. Worldwide the median is 3.7 s, 99% under 36 s. readsb thins steady flight, so turns get more
   points than straight legs.
3. **Geometric altitude is ellipsoidal (WGS84).** 25 aircraft rolling out on runways at Rocky Mountain
   Metropolitan (KBJC), from their last reported altitudes before readsb marked them on the ground. Each was
   compared with the 1 m USGS 3DEP elevation (NAVD88) at its position. Geometric altitude minus
   (3DEP + GEOID18 undulation, −16.7 m): median −0.7 m, interquartile range −1.3 to +1.5 m. Against 3DEP alone
   (an MSL reading) it is −17.5 m. The values come in 25 ft (7.6 m) steps, so a single point is only good to
   about ±4 m. Present on 99% of airborne ADS-B points here.
   - **MLAT:** geometric altitude present on only 15% of MLAT points. Where present, it stayed unchanged on 45%
     of steps in which the barometric altitude moved by 15 m or more (ADS-B: 2%), so it is held over from
     earlier messages. Hence `mlat_geometric_alt: false`.
   - **Air/ground:** many GA aircraft report airborne while rolling on the runway; on-ground points carry no
     altitude.
4. **Time.** The whole day (3.6 GB) streamed and decoded in 524 s, about 7 MB/s; the download is the likely
   bottleneck, so no parallel parse yet. The ingest itself on the 100 MB subset took 29 s.
5. **Volume near the region.** A 30 km margin around `synth_boulder`, for the whole day: 380,389 fixes from
   1,001 aircraft (6 MB Parquet). Sources after filtering: ADS-B 93%, ADS-R 6%, MLAT 0.7%, TIS-B 0.1%.
6. **Types and classes.** 944 of the 1,001 aircraft carry a type code (`t`). With
   `aircraft_types.type_table_path` set to the Doc 8643 table, the day's 2,031 segments split prop 1,070,
   jet 829, unknown 132. With it unset (the current default) all are unknown, as §3 says.

Still open:
- **The `mlatonly` variant:** not documented in the README, and not downloaded.
- **Wider checks:** one airport and one day. A second area (and the OpenSky comparison for the same hour)
  would show whether coverage and cadence hold away from Denver.

## 5. Not built
- A downloader. The stdin form in §3 makes one unnecessary, and each download needs the user's approval.
- A live-API logger for field sessions (§1).
- Switching the default registry to adsb.lol types.
