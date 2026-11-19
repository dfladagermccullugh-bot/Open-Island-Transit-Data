# Codebook

What each published file contains. Derived tables (parsed positions, stop
events, quality metrics, on-time measures) will be added here as they are
published.

## Where the files are

- **GitHub Releases**, one release per month named `raw-YYYY-MM`. Each
  service day adds the files below.
- **`data` branch**, `manifest/raw.json`: every archived day, its row
  counts, and the size and SHA-256 of each file.

A service day is a calendar date in `America/Los_Angeles`. A row belongs to
the day in which it was logged. All timestamps are UTC.

## `raw-YYYY-MM-DD.parquet`

One row per fetch attempt, including failed ones. This file is the source of
truth: everything else can be rebuilt from it.

| Column | Type | Meaning |
|---|---|---|
| `fetch_id` | integer | Unique, increasing id of the attempt |
| `feed` | text | `vehiclePositions`, `tripUpdates` or `serviceAlerts` |
| `scheduled_at` | timestamp | When the scheduler fired |
| `requested_at` | timestamp | When the request was sent. Empty for ticks skipped during backoff |
| `responded_at` | timestamp | When the whole response had arrived. Empty if there was none |
| `latency_ms` | integer | `responded_at` minus `requested_at` |
| `http_status` | integer | HTTP status. Empty if no response arrived |
| `bytes` | integer | Size of the response body |
| `sha256` | text | SHA-256 of the response body |
| `header_ts` | timestamp | The feed's own header timestamp. In practice this equals the request time, so it does not show whether the data is fresh |
| `entity_count` | integer | Number of entities in the feed message (vehicles, trip updates or alerts) |
| `newest_entity_ts` | timestamp | Latest vehicle or trip-update timestamp in the message |
| `error_class` | text | Empty for success. `timeout`, `network` (no response), `http` (status other than 200), `decode` (body is not a feed message), `backoff_skip` (tick skipped after repeated failures) |
| `error_msg` | text | Detail for the error |
| `collector_version` | text | Version of the collector code that made the attempt |
| `logged_at` | timestamp | When the row was written |
| `body` | bytes | The response body exactly as received (a GTFS-Realtime protobuf message when the fetch succeeded). Empty when there was no response |

To decode a body, use any GTFS-Realtime library, or `pipeline/parse/realtime.py`
in this repository.

## `fetch-log-YYYY-MM-DD.csv`

The same rows and columns as the Parquet file, without `body`.

## `static-YYYY-MM-DD-<source>-<id>.zip`

A static GTFS download exactly as received. `source` is `passio` (downloaded
twice a day) or `trillium` (weekly). The Passio zip has been observed
arriving without its calendar files and central directory; such files are
archived as they came. `pipeline/gtfs/zipread.py` reads them.

## `manifest/raw.json`

| Field | Meaning |
|---|---|
| `days.<date>.fetch_rows` | Fetch attempts that day |
| `days.<date>.raw_rows` | Attempts that have a stored body |
| `days.<date>.ok_rows` | Attempts with HTTP 200 and no error |
| `days.<date>.digest` | MD5 of `fetch_id:sha256` for every row with a body, comma-joined in `fetch_id` order. The collector computes the same value before it deletes its copy |
| `days.<date>.files` | Name, size and SHA-256 of each file |
| `days.<date>.static` | The static GTFS downloads of that day, with the zip integrity check results |
| `days.<date>.release_tag` | The release holding the files |
| `days.<date>.code_sha` | Commit of the code that built the files |

## The collector's live tables

Readable with the public key in `.env.example` until a day is archived and
pruned: `fetch_log` (kept permanently), `raw_export` (rolling), `static_export`
(rolling), `status_now`, and the functions `day_digest(date)` and
`collector_health()`.
