# pyopensky: what adding it would pull in (for the user's approval; not installed)

CLAUDE.md asks for any new dependency to be explained first. If pyopensky pulls in pyarrow, Parquet switches
from fastparquet to pyarrow, and fastparquet (with cramjam) is dropped rather than keeping both. Figures are
from PyPI metadata on 2026-11-04. Installed sizes are estimates; measure them in a scratch venv before
deciding.

## What it is
- pyopensky **2.18** (released 2026-10-25), MIT licence, Python ≥ 3.10.
- It is the OpenSky Network's client for the Trino historical database (and the live REST API).
- It reads credentials from its own config file or environment. Those never go into this repo
  (CONTRIBUTING §2).

## Direct requirements (no extras)
| Package | Why pyopensky needs it | Already in our venv? | Size (macOS arm64 cp312 wheel) |
|---|---|---|---|
| **pyarrow ≥ 17** (26.0.1 now) | Arrow/Parquet result handling | no | **37 MB wheel**; installed roughly 100–150 MB |
| trino[sqlalchemy] ≥ 0.329 (0.340.0) | the Trino client | no | 64 kB, plus its own deps below |
| httpx, minio, pyjwt, stamina, appdirs, python-dotenv, tqdm | HTTP, the S3 client, auth tokens, retries, config, progress bars | no | each < 0.1 MB |
| numpy ≥ 2.0.2, pandas ≥ 2.2.2 | data frames | yes (2.5.4, 3.1.0) | – |
| typing-extensions, tzdata | | yes | – |

## Transitive requirements
- **trino:** lz4, orjson, python-dateutil (have it), pytz, requests, tzlocal and zstandard. The `[sqlalchemy]`
  extra adds **SQLAlchemy 2.x** (2.5 MB wheel) and greenlet.
- **httpx:** anyio, certifi (have it), httpcore, h11, idna.
- **minio:** argon2-cffi (with argon2-cffi-bindings), pycryptodome and urllib3.
- **stamina:** tenacity.
- **requests:** charset-normalizer.

**Roughly 25 new packages and about 150–200 MB in the venv**, nearly all of it pyarrow. All of them are
permissive (MIT, BSD, Apache-2.0, MPL-2.0 for certifi, tqdm and orjson).

## Consequences
1. **Parquet: fastparquet → pyarrow** (CLAUDE.md rule).
   - `doppler/export.py` `write_parquet` / `read_parquet` (lines 39–55) switch to pyarrow, still zstd and
     still only the needed columns.
   - The two fastparquet-specific asserts in `tests/test_export.py` (codec enum, `ParquetFile`) are rewritten
     for `pyarrow.parquet`.
   - `fastparquet` and `cramjam` leave `requirements.txt`.
   - The Parquet files already written stay readable: pyarrow reads fastparquet output. A round-trip test
     on an existing run's `tracks.parquet` confirms it.
   - Side benefit: the pandas 3.1 deprecation warnings from inside fastparquet disappear (HANDOFF, Fragile).
2. **pandas behaviour changes when pyarrow is present.** pandas 3's default `str` dtype is backed by pyarrow
   when it is installed, and by Python objects otherwise. The pipeline's string columns are icao24, callsign
   and the class labels. Run the full test suite (fast and slow) after the switch. The Stage A reference rows
   should not change; check that on ≥ 5 seeds (plan §8.3 step 6 rule for identical-result changes).
3. **CI** installs about 40 MB more, cached by pip. That costs seconds, not minutes.
4. **Alternatives, if the weight is unwelcome:**
   - (a) Use `trino` directly, without pyopensky. That means our own OAuth token handling (OpenSky's Trino
     uses an OpenID token), roughly 30 lines in `cli.py`. trino itself does not need pyarrow, so Parquet could
     stay on fastparquet.
   - (b) Run pyopensky in a separate tool venv that writes cropped Parquet or CSV, which the pipeline then
     reads. The pipeline's dependencies stay unchanged.
   - (c) No Trino at all: the scientific-dataset fallback (`docs/opensky_ingest.md`) needs no new package
     for CSV.

## Recommendation
Add pyopensky, pinned, together with the pyarrow switch in one reviewed commit when P4.3 starts. Don't add
it earlier. Both `Trino.history()` and `query()` keep OpenSky's raw column names, which
`tracks.parse_opensky_states` already maps (checked in pyopensky v2.18's source). The traffic library is
what renames columns and converts units; it is not needed. Results come back with pyarrow-backed dtypes.
Add a Trino-shaped test (Arrow timestamps, nullable booleans) when pyarrow lands. If the user prefers a
lean environment, (b) is the cleanest.
