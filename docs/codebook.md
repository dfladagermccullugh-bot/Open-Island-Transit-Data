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
  service day. `crosswalk/`: links between the Passio and Trillium static
  feeds. `timepoints/`: true timepoints from the printed timetables.
  `quality/`: daily feed-quality metrics. `ontime/`: experimental daily
  on-time summaries. `manifest/events.json`: the stop-event files.

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

## `timepoints/<passio version>__timetable-<date>-<hash>/`

Built by `pipeline/gtfs/timepoints.py` once per pair of Passio version and
printed timetable in effect. `<date>` is the timetable's effective date;
`<hash>` covers the reference files and the matching code, so any change to
either produces a new folder.

The printed timetables are Island Transit's service guides; the stops they
print are the published timepoints. They are kept in `reference/` (see its
README). Each printed name is matched to a Passio stop on the same route by
name similarity, or by a reviewed override; names that fit more than one
stop, or none, are left unresolved rather than guessed.

| File | Contents |
|---|---|
| `timepoints.csv` | One row per printed stop and matched Passio stop: route, day type, direction, printed order and name, `passio_route_id`, `passio_stop_id`, `passio_stop_name`, and `match` (`name <score>`, `override`, or the reason it is unresolved) |
| `summary.json` | Reference date, printed names, resolved names, the unresolved names with reasons, and the number of timepoint route-stop pairs |

## Stop events (experimental)

Built nightly by `pipeline/events/infer.py` (method: `docs/methods.md`,
section 3) and stored in the `parsed-YYYY-MM` release. Experimental until
the field validation in `docs/methods.md`, section 5.

### `stop-events-YYYY-MM-DD.parquet`

One row per scheduled stop of every scheduled trip in the Passio schedule
for the day, observed or not.

| Column | Meaning |
|---|---|
| `trip_id`, `route_id`, `stop_sequence`, `stop_id` | Passio ids |
| `vehicle_key` | The vehicle whose reports were used; empty if none carried the trip id |
| `is_first_stop`, `is_last_stop` | Position in the trip |
| `is_timepoint` | A true timepoint (`timepoints/`) |
| `stop_distance_m` | The stop's distance along the trip's shape |
| `scheduled_arrival`, `scheduled_departure` | Passio schedule, as UTC timestamps |
| `arrival_time` | When the bus first came within 40 m of the stop along the shape |
| `departure_time` | When it moved more than 40 m past the stop |
| `arrival_gap_s`, `departure_gap_s` | Seconds between the two position reports the time was interpolated from |
| `arrival_status`, `departure_status` | `observed`; `gap` (reports more than 120 s apart, no time given); `not_reached` (tracking ended before the stop); `past_when_first_seen`; `skipped` (the bus left out the part of the shape the stop is on); `no_shape` (the trip has no shape); `no_reports` (no vehicle carried the trip id); `not_applicable` (no arrival at the first stop, no departure at the last) |

### `trip-tracks-YYYY-MM-DD.parquet`

One row per scheduled trip: the vehicles that carried its id, how many of
their position reports were used, and why the rest were not
(`reports_before_start`, `reports_after_end`, `off_shape_reports` more than
100 m from the shape, `wrong_direction_reports`), how far along the
shape the bus was followed, and the parts of the shape it left out
(`shape_skips`, `skipped_m`). `shape_length_m` is after collapsing
retraced segments (`docs/methods.md`, section 3).

## `ontime/` (experimental)

`ontime/days/YYYY-MM-DD.json` and `ontime/daily.csv`, from
`pipeline/events/ontime.py` (`docs/methods.md`, section 4). Counts are of
scheduled timepoint events: a trip's scheduled time at one of its true
timepoints. `observed_share` is always reported next to the on-time share;
events with no inferred time are never counted as late. Events scheduled
before collection began are left out. Each day file also breaks the counts
down by route and by Pacific hour. `validation` holds the automatic checks of the method
(`pipeline/events/validate.py`, `docs/methods.md`, section 5): agreement of
inferred departures with the feed's `current_stop_sequence`, overall and by
route, and segment travel times that run backwards or exceed 35 m/s, with
the fastest listed. `daily.csv` carries the agreement share as
`feed_agreement_share`.
- `ontime/field-check.json` (`pipeline/events/field.py`): the field check
  (`docs/methods.md`, section 5, check 1). `overall`, `arrival`,
  `departure`, `pass` (bus level with the stop, against the midpoint of the
  inferred arrival and departure) and `by_stop` give the number of timed moments scored, bias,
  median and mean absolute difference (inferred minus timed, seconds) and
  the shares within 30 s and 60 s; `meets_target` compares the mean absolute
  difference with 30 s; `pairs` lists every comparison. Rows whose day is
  not processed yet, that match no scheduled event, or whose moment has no
  inferred time are counted separately.

## `quality/`

Computed by `pipeline/quality/metrics.py`, `pipeline/quality/stops.py` and `pipeline/quality/gaps.py` for each service day once its raw
file, parsed tables and pinned schedule exist. Definitions are in
`docs/methods.md`, section 2.

- `quality/days/YYYY-MM-DD.json`: every metric for the day, grouped as
  `paradata`, `nonresponse`, `coverage`, `measurement`, `processing`,
  `events` (metrics from inferred stop events, experimental; empty if the
  day has no stop events), plus the schedule used (`schedule`) and the
  SHA-256 of the input files (`inputs`). A day is recomputed when
  `QUALITY_VERSION` changes or its stop events are rebuilt.
  `coverage.trips` (from `quality/0.3.0`) lists every scheduled trip that
  could be observed, as `columns` and `rows`: `trip_id`, `route_id`,
  `start_hour` (Pacific hour of the first departure), `scheduled_in_service`
  (scheduled trips in service at that moment, the trip included) and
  `observed` (any position carried its `trip_id`).
- `quality/daily.csv`: one row per day with the headline figures, rebuilt
  from the day files.
- `quality/gaps.json` (`pipeline/quality/gaps.py`): randomness of gaps over
  all days with trip rows. `rates` gives scheduled and unobserved trips by
  hour, hour band, route, day type and trips in service. `model` is a
  logistic regression of "trip not observed" (Firth penalised likelihood)
  with odds ratios, 95% intervals, p-values and a likelihood-ratio test of
  all predictors; it stays `null` until there are 14 days and 30 unobserved
  trips (`model_status` says which).

| `daily.csv` column | Meaning |
|---|---|
| `passio_version` | Static version the day was measured against |
| `scheduled_trips` | Passio trips scheduled that day (on the first day, only those still running when collection began) |
| `trip_coverage` | Share of those trips with at least one vehicle position |
| `vehicle_uptime`, `trip_update_uptime` | Share of 20-second slots with a successful fetch |
| `vehicle_fetch_failure_rate` | Share of vehicle-position fetches that failed |
| `empty_in_service_rate` | Successful vehicle-position fetches with no vehicles, while a trip was scheduled |
| `stale_in_service_rate` | Successful vehicle-position fetches whose newest vehicle timestamp was over 120 s old, while a trip was scheduled |
| `longest_gap_s_median` | Median over observed trips of the longest interval between new position reports |
| `position_age_s_median`, `position_age_s_p95` | Time from the vehicle's timestamp to the first fetch that returned it |
| `impossible_jumps` | Consecutive reports of one vehicle implying more than 130 km/h |
| `timepoint_coverage` | Scheduled timepoint events with an inferred time (experimental) |
| `stop_coverage` | The same at every stop (experimental) |
| `off_shape_share` | Position reports in a trip's window more than 100 m from its shape |
| `prediction_mae_s_5min` | Mean absolute error of arrival predictions published 5 minutes ahead, seconds (experimental) |
| `prediction_within_60s_5min` | Share of those predictions within 60 s (experimental) |
| `jitter_m_p95` | 95th percentile of position spread while a bus stands at a terminal, metres |
| `unassignable_trip_ids` | Trip ids reported that are not in the static version or not scheduled that day |
| `vehicles_missing_vs_30d` | Vehicles seen in the previous 30 days (with quality files) but not this day |
| `trip_id_match_rate` | Positions whose `trip_id` exists in the static version |
| `over_coverage_share` | Positions with no trip, an unknown trip, or a trip not scheduled that day |
| `vehicle_latency_ms_median` | Median response time of the vehicle-position feed |
| `vehicles_seen` | Distinct vehicles reporting |
| `timetable_reference` | Effective date of the printed timetable used for timepoints |
| `timepoint_names_resolved_share` | Printed timepoint names tied to a Passio stop |

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
| `days.<date>.crosswalk` | Folder under `crosswalk/` for that day's pair of versions |
| `days.<date>.timepoints` | Folder under `timepoints/` for that day's Passio version and printed timetable |

## `crosswalk/<passio version>__<trillium version>/`

Built by `pipeline/gtfs/crosswalk.py`, once per pair of pinned versions. The
real-time feed uses Passio ids. Trillium's timepoint flags are kept for
comparison only; the true timepoints are in `timepoints/`.

| File | One row per | Contents |
|---|---|---|
| `stops.csv` | Passio stop | Matched Trillium stop, `method` (`stop_code`: same code within 150 m; `distance`: nearest Trillium stop within 60 m; empty: unmatched), distance, and the nearest Trillium stop either way |
| `routes.csv` | Passio route | Matched Trillium route, by short name. Passio's three NAS Whidbey routes have no short name and map to Trillium's single NAS route |
| `timepoint_stops.csv` | Passio route and stop | `trillium_timepoint`: Trillium marks the matched stop as a timepoint on at least one trip of the matched route. Also the counts of Passio stop times, Trillium stop times and Trillium timepoint stop times behind that decision |
| `schedule_agreement.csv` | Passio stop time at a Trillium timepoint | `nearest_trillium_minus_passio_s`: seconds from the Passio time to the nearest Trillium timepoint time at that stop and route, on a day of the week both serve. Empty if Trillium has none |
| `summary.json` | | Counts of the above |

## The collector's live tables

Readable with the public key in `.env.example` until a day is archived and
pruned: `fetch_log` (kept permanently), `raw_export` (rolling), `static_export`
(rolling), `status_now`, and the functions `day_digest(date)` and
`collector_health()`.
