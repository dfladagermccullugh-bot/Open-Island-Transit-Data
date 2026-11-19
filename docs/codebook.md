# Codebook

What each published file contains. Further derived tables (stop events,
quality metrics, on-time measures) will be added here as they are published.

## Where the files are

- **GitHub Releases**, one release per month named `raw-YYYY-MM` for the raw
  archive and `parsed-YYYY-MM` for the parsed tables. Each service day adds
  the files below.
- **`data` branch**, `manifest/raw.json` and `manifest/parsed.json`: every
  archived day, its row counts, and the size and SHA-256 of each file.
  `manifest/static.json`: static GTFS versions and the version pinned to each
  service day.

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

## Parsed tables

Built nightly from `raw-YYYY-MM-DD.parquet` by `pipeline/tables/build.py`,
one Parquet file per table per service day, in the `parsed-YYYY-MM` release.
Every field the feed can carry is a column; a field the feed did not send is
empty (null), never filled in.

**Runs.** The feeds are fetched every 20 seconds but most values change less
often. Each row is a *run*: consecutive successful fetches of the same feed in
which one item (a vehicle, a trip, a stop prediction, an alert) reported
exactly the same values. A run ends when any value changes, when the item is
missing from a fetch, or when a fetch of that feed fails. Every table ends
with these columns:

| Column | Type | Meaning |
|---|---|---|
| `first_fetch_id`, `last_fetch_id` | integer | First and last fetch of the run; join to the fetch log |
| `first_requested_at`, `last_requested_at` | timestamp | When those fetches were requested |
| `times_seen` | integer | Number of fetches in the run |

Times sent by the feed (`vehicle_ts`, `trip_update_ts`, `arrival_time`,
`departure_time`, alert periods) are converted from Unix seconds to UTC
timestamps. The feed's entity ids are positions in the message and change
when vehicles come and go, so they are not kept.

### `vehicles-YYYY-MM-DD.parquet`

One row per vehicle per run, which in practice means one row per vehicle per
position report (`vehicle_ts`).

| Column | Meaning |
|---|---|
| `vehicle_key` | `vehicle_id`, or `entity:<id>` if the feed sent no vehicle id |
| `vehicle_id`, `vehicle_label` | Vehicle id and the label shown on the bus |
| `vehicle_ts` | Time of the position report, as sent by the vehicle |
| `trip_id`, `route_id`, `direction_id`, `start_date`, `start_time`, `trip_schedule_relationship` | The trip the vehicle reports. `direction_id`, `start_date` and `start_time` have not been sent so far |
| `lat`, `lon`, `bearing`, `speed`, `odometer` | Position; speed in metres per second |
| `current_stop_sequence`, `stop_id`, `current_status` | The stop the vehicle reports. `current_status` has not been sent so far |
| `congestion_level`, `occupancy_status`, `occupancy_percentage` | As sent |

### `trip-updates-YYYY-MM-DD.parquet`

One row per trip per run.

| Column | Meaning |
|---|---|
| `trip_key` | `trip_id`, or `entity:<id>` if the feed sent no trip id |
| trip columns | As in `vehicles` |
| `vehicle_id` | Vehicle assigned to the trip. The feed also lists upcoming trips that have no vehicle yet |
| `trip_update_ts` | Time of the update |
| `trip_delay` | Trip-level delay in seconds, if sent |
| `stop_time_update_count` | Number of stops with a prediction |

### `trip-update-stops-YYYY-MM-DD.parquet`

One row per predicted stop per run: a prediction for one stop of one trip,
for as long as it stayed unchanged.

| Column | Meaning |
|---|---|
| `trip_key`, trip columns, `vehicle_id`, `trip_update_ts`, `trip_delay` | As in `trip-updates` |
| `stop_sequence`, `stop_id` | The stop |
| `arrival_time`, `departure_time` | Predicted times |
| `arrival_delay`, `departure_delay`, `arrival_uncertainty`, `departure_uncertainty` | In seconds, if sent |
| `stop_schedule_relationship` | As sent |

### `alerts-YYYY-MM-DD.parquet`

One row per alert per run, keyed by the alert's `entity_id`.

| Column | Meaning |
|---|---|
| `entity_id` | The alert's id in the feed |
| `active_periods` | List of start and end times |
| `cause`, `effect` | As sent |
| `header`, `description`, `url` | English text (or the first translation) |
| `route_ids`, `stop_ids`, `trip_ids` | What the alert applies to |

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

## `manifest/parsed.json`

| Field | Meaning |
|---|---|
| `days.<date>.tables_version` | Version of the table code. A day is rebuilt when this changes |
| `days.<date>.rows` | Rows in each table |
| `days.<date>.fetches` | Per feed: fetches attempted, parsed, failed (no usable response), and bodies that could not be decoded |
| `days.<date>.duplicate_keys` | Items that appeared twice in one message (both are kept) |
| `days.<date>.raw_sha256` | SHA-256 of the raw file the tables were built from |
| `days.<date>.files`, `release_tag`, `code_sha` | As in `manifest/raw.json` |

## `manifest/static.json`

Built by `pipeline/gtfs/versions.py` from the static downloads in the raw
archive.

A **version** is identified by the SHA-256 of every file in the zip except
`feed_info.txt`, whose dates roll forward each day (Passio) or record when the
file was generated (Trillium). Its id is `<source>-<first 12 hex digits>`. A
download is **complete** when the zip is intact and has every required GTFS
file; only complete downloads define versions.

| Field | Meaning |
|---|---|
| `versions.<id>.source` | `passio` or `trillium` |
| `versions.<id>.files` | SHA-256 of each file (except `feed_info.txt`) |
| `versions.<id>.feed_info` | `feed_info.txt` of the first download of this version |
| `versions.<id>.first_seen`, `last_seen`, `first_day` | When this version was downloaded |
| `versions.<id>.example` | A release file holding this version |
| `versions.<id>.downloads` | Ids of the complete downloads with this content |
| `downloads.<id>` | Each download: day, time, release file, `complete`, `problems`, `missing` files, and `version` (empty if incomplete) |
| `days.<date>.<source>.version` | The version pinned to that service day |
| `days.<date>.<source>.basis` | Which rule chose it: `complete download that day` (the first one); `incomplete download that day, matched to a complete version` (every file it had was identical to that version's); `carried forward from <date>` (the latest earlier complete download); `no complete version yet` |
| `days.<date>.<source>.changed_during_day` | True when downloads on that day disagree |

## The collector's live tables

Readable with the public key in `.env.example` until a day is archived and
pruned: `fetch_log` (kept permanently), `raw_export` (rolling), `static_export`
(rolling), `status_now`, and the functions `day_digest(date)` and
`collector_health()`.
