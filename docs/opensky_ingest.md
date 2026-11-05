# OpenSky ingest: datasets, formats and the mapping onto the track schema

Plan §8.3 step 4, P4.3 and P5.2. The user's OpenSky account (Trino) is the primary source. The scientific
datasets at https://opensky-network.org/data/scientific are the fallback (HANDOFF, plan §7). **No data is
downloaded without the user's approval** (U14).

## 1. What the page offers (read 2026-11-04)
Eleven datasets are listed. Three are relevant here.

| Dataset | Contents | Relevance |
|---|---|---|
| **1. Weekly 24 h of state vectors, 2017–2022** | every Monday's full state vectors, 10 s update interval; one file per hour in CSV, Avro or JSON | real traffic for P4.3 replay / P5.2 with injected events. Ends in 2022, so it can't cover new field recordings |
| **5. Aircraft metadata database** | icao24 → registration, typecode, operator, ...; CSV; monthly snapshots ("updates are currently on hold") | the registry for `aircraft_types.build_lookup` |
| **11. One-day Trino tables snapshot (1 March 2026)** | the same tables as the Trino interface (state vectors, flights, ...) as a static download | one recent day at Trino resolution; size not stated |

The rest are raw Mode S samples, LocaRDS, COVID-19 flights, emergencies, climbing segments, GICB
capabilities, ADS-C and the PRC 2024 challenge. None of them is needed.

**State on 2026-11-04 (checked with the S3 listing of `s3.opensky-network.org/data-samples`):**
- `states/` (dataset 1) and `trino-tables/` (dataset 11) hold only a zero-byte `placeholder.txt`, written at
  about 21:35 UTC that day. The files appear to be moving, and neither dataset can be downloaded right now.
- The page's "full description" link (`datasets/states/README.txt`) returns 404. The column definitions
  below come from the Internet Archive's copy of that README (captured 2026-09-19).
- `metadata/` is intact. It has:
  - `aircraft-database-complete-YYYY-MM.csv`; the newest is **2025-08, 108 MB**;
  - `doc8643AircraftTypes.csv`, **0.7 MB** (ICAO type designator → description such as `L1P`);
  - the older, incomplete `aircraftDatabase*.csv`. The bucket's README warns that these are incomplete, and
    that the "complete" dumps use different columns.

**File layout of dataset 1 (from earlier use; not verifiable today):**
- one tar per hour: `states/<YYYY-MM-DD>/<HH>/states_<YYYY-MM-DD>-<HH>.csv.tar`;
- the tar holds a gzipped CSV and the licence text.

Re-check the layout and the per-hour size once the folder is populated again, before asking to download.

**Terms:** the README asks users to agree to the terms in the dataset's `LICENSE.txt`. That file could not
be read today, so read it before any use, and cite OpenSky as it asks.

## 2. Proposal
1. **Now (for approval):** the aircraft metadata, so that real tracks get prop/jet classes:
   - `aircraft-database-complete-2025-08.csv` (108 MB). Keep only `icao24` and `typecode` (plus the
     aircraft-class column if present) as a small zstd Parquet in `data/`, then delete the CSV
     (CLAUDE.md Storage);
   - `doc8643AircraftTypes.csv` (0.7 MB), the `type_table` for `build_lookup`.

   Check the complete dump's header on download. The README says its columns differ from the old dumps.
   `build_lookup` needs `icao24` and `typecode`. The description comes from Doc 8643.
2. **State vectors for any date, including field-recording dates: Trino**, through the user's account, from
   the `state_vectors_data4` table (one state vector per aircraft per second). It has the same column names
   as the CSV, so the mapping below applies unchanged. Crop in the SQL to the region bounding box, the time
   window and the columns in §3.
3. **Fallback for P4.3 / P5.2 only:** one Monday hour of dataset 1, in CSV, not Avro. Avro would need a new
   dependency (fastavro). Use it only once the folder is back and its size is known. It has 10 s cadence (see
   §4.1) and ends in 2022.
4. **Field recordings without Trino:** the scientific datasets can't cover them. Log traffic during each
   session instead, with a local receiver (also P5.3, `docs/uat_coverage.md`), or by polling the live OpenSky
   REST API for the bounding box. Either needs its own small logger, and the user's approval.

## 3. Mapping onto the track schema (`doppler/tracks.py`)
State-vector tables are recognised by their `time`, `lastposupdate` and `lastcontact` columns, then typed by
`parse_opensky_states`:
- blanks become NaN;
- `onground`, `alert` and `spi` become bool;
- `squawk` stays a 4-digit string (CSV readers turn `0123` into 123).

Units are already SI, so nothing is converted.

| OpenSky column | Canonical | Units | Notes |
|---|---|---|---|
| `time` | `time_utc` | epoch s | the snapshot time; not the position time |
| `lastposupdate` | `t_s` | epoch s | **the position time** (since session 3). Repeated stale positions are dropped (`icao24`, `t_s` duplicates) |
| `lastcontact` | kept | epoch s | diagnostic only. OpenSky keeps emitting vectors for up to 300 s after the last contact; the de-duplication above removes them |
| `icao24` | `icao24` | – | lower-cased |
| `callsign` | `callsign` | – | trailing spaces stripped |
| `lat`, `lon` | `lat`, `lon` | deg, WGS84 | |
| `velocity` | `ground_speed_mps` | m/s over ground | what the Doppler v tracks (CLAUDE.md Wind pitfall) |
| `heading` | `track_deg` | deg from true north | the README says it is the track angle, not heading |
| `vertrate` | `vertical_rate_mps` | m/s | |
| `geoaltitude` | `geometric_alt_m` | m | treated as ellipsoidal height. **The datum is not stated**: check it against runway elevation on short final (P5.4, plan open question 2) |
| `baroaltitude` | `baro_alt_m` | m | pressure altitude (standard atmosphere). Used only when < 50 % of fixes have geoaltitude; needs P3.2's correction or the wider `baro_alt_tol_m` |
| `onground` | `onground` | bool | kept and flagged; no filter yet (§4.3). Sources without it get False |
| `squawk`, `alert`, `spi` | kept | – | unused |
| `serials`, `hour` (Trino only) | kept | – | unused |

**traffic/pyopensky names are refused.** pyopensky's `history()` renames the columns (`altitude`,
`groundspeed`, `vertical_rate`, `track`, ...), and traffic converts them to ft, kt and ft/min. A frame with
`altitude`, `groundspeed` or `vertical_rate` raises `ValueError` unless `tracks.columns` maps those columns
explicitly. Otherwise a feet value could pass silently as metres, because `geoaltitude` keeps its name. Prefer
pyopensky's raw SQL `query()`, which keeps the OpenSky names.

Tests: `tests/test_opensky.py`, on a 14-row hand-made CSV in the documented layout.

## 4. Caveats for real data
1. **Velocity time.** In a state vector, `velocity`, `heading` and `vertrate` are the latest values at the
   snapshot (about `lastcontact`), not at `lastposupdate`. With 1 s Trino data the gap is under a second.
   With the datasets' 10 s cadence it can reach several seconds, which matters on turns (3°/s × 10 s = 30°).
   For 10 s data, consider `tracks.velocity_source: derivative`. That is a config decision; measure it on
   P4.3 first.
2. **Cadence.** 10 s fixes are within `max_gap_s` (20 s), so segments do not split. But interpolation error
   on turns and climbs is larger than with 1 s data.
3. **Surface positions** usually carry no barometric altitude, so they drop out at the altitude step anyway.
   The ones that remain are take-off rolls, landings and taxiing fixes that report an altitude. They are
   kept, and flagged in the normalised frame's `onground` column. Near an airport, a taxiing aircraft inside
   the audibility slant distance could trigger the silence filter, so dropping them is for the user to
   decide. The proposal is a `tracks.drop_onground` key in config.yaml plus a `TrackParams` field, added
   together (`tests/test_config.py` requires every parameter to be in config.yaml). It is not added here,
   because config.yaml belongs to another session.
4. **Coverage.** OpenSky's state vectors come from its 1090 MHz network. Expect UAT-only GA aircraft
   (978 MHz, common in the US) to be missing; P5.3 measures how many. Low-altitude coverage near KBJC/KBDU
   is also unmeasured.
5. **I/O is not written yet.** Reading the tar / `.csv.gz` or a Trino result, cropping it to region and time,
   and writing zstd Parquet with only the §3 columns belongs in `cli.py`. That file is owned by another
   session now. `cli.load_tracks` already reads a CSV or Parquet in OpenSky naming through
   `tracks_from_frame`.
