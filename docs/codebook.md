# Codebook

This page describes every file we publish and every column inside it.

## Where the files are

The big files live in GitHub Releases, one release per month. The raw
archive goes in `raw-YYYY-MM` and the parsed tables go in `parsed-YYYY-MM`,
and each service day adds the files described below. Once a month is over,
its releases also get a checksum file, `SHA256SUMS-<release>.txt`, so you can
check your downloads (see `reports/`).

The smaller files live on the `data` branch of the repository. There,
`manifest/raw.json` and `manifest/parsed.json` list every archived day with
its row counts and the size and SHA-256 fingerprint of each file, and
`manifest/events.json` does the same for the stop-event files.
`manifest/static.json` lists the versions of the static schedule and which
one applied to each service day. The `crosswalk/` folder links the Passio and
Trillium schedule files, `timepoints/` holds the true timepoints taken from
the printed timetables, `quality/` holds the daily feed-quality figures,
`ontime/` holds the daily on-time summaries, `maps/` holds the route lines
the site draws, and `reports/` holds one report for each complete month.

Two files at the top of the `data` branch help a program find its way.
`files.csv` lists every file in the Releases, one row each, with its service
day, the kind of table it holds, its release, name, size, SHA-256 and the
address to download it from. `datapackage.json` is this codebook in a form a
program can read: every table with its columns, their types and units, in
the Data Package format. Both are written by `pipeline/datapackage.py`.

`reference/no-service-days.csv`, in the main branch, lists the holidays on
which Island Transit runs no service. On those days the day files carry
`service_expected: false`, and every figure that pools several days leaves
them out.

A service day is a calendar date in Pacific time (`America/Los_Angeles`).
Each row belongs to the day in which it was logged, and every timestamp is
in UTC. Three things are in Pacific time instead, because they describe the
schedule as riders see it: hours of the day (every `by_hour` section and
`start_hour`), the kind of day (`day_type`), and scheduled clock times
copied from the schedule files (`passio_time`).

In the CSV files on the `data` branch, a missing value is an empty cell,
true and false are written `true` and `false`, and no number is written in
scientific notation (`0.000063`, not `6.3e-05`). The files written before
November 22, 2026 had Python's `True`, `False` and `6.3e-05`; the values are
the same, and the files were rewritten in the new form that night
(`format/0.2.0`, `pipeline/common.py`).

## `raw-YYYY-MM-DD.parquet`

This file has one row for every time the collector asked the feed for
data, including the times it failed. It is the source of truth, and every
other file can be rebuilt from it.

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

To decode a body, you can use any GTFS-Realtime library or
`pipeline/parse/realtime.py` in this repository.

## `fetch-log-YYYY-MM-DD.csv`

This file has the same rows and columns as the Parquet file, minus the
`body` column.

## `static-YYYY-MM-DD-<source>-<id>.zip`

Each of these is a schedule download saved exactly as it arrived. The
`source` is either `passio`, which we download twice a day, or `trillium`,
which we download weekly. The Passio zip sometimes arrives without its
calendar files and its central directory (the index a zip file normally ends
with). We archive those files as they came, and `pipeline/gtfs/zipread.py`
can still read them.

## Parsed tables

Each night, `pipeline/tables/build.py` reads `raw-YYYY-MM-DD.parquet` and
writes one Parquet file per table per service day into the `parsed-YYYY-MM`
release. Every field the feed is able to send gets its own column. When the
feed didn't send a field, that cell is left empty (null), and we never fill
it in.

We fetch the feeds every 20 seconds, but most values change less often than
that, so each row is a run. A run is a stretch of successful fetches in a row
in which one item, such as a vehicle, a trip, a stop prediction or an alert,
reported exactly the same values. The run ends when any value changes, when
the item drops out of a fetch, or when a fetch of that feed fails. Every
table ends with these columns:

| Column | Type | Meaning |
|---|---|---|
| `first_fetch_id`, `last_fetch_id` | integer | First and last fetch of the run; join to the fetch log |
| `first_requested_at`, `last_requested_at` | timestamp | When those fetches were requested |
| `times_seen` | integer | Number of fetches in the run |

We convert the times the feed sends (`vehicle_ts`, `trip_update_ts`,
`arrival_time`, `departure_time` and alert periods) from Unix seconds to UTC
timestamps. We don't keep the feed's entity ids, because they only number
the items within each message and shift whenever vehicles come and go.

### `vehicles-YYYY-MM-DD.parquet`

This table has one row per vehicle per run. In practice that comes to
one row for each position report a vehicle sends (`vehicle_ts`).

| Column | Meaning |
|---|---|
| `vehicle_key` | `vehicle_id`, or `entity:<id>` if the feed sent no vehicle id |
| `vehicle_id`, `vehicle_label` | Vehicle id and the label shown on the bus |
| `vehicle_ts` | Time of the position report, as sent by the vehicle |
| `trip_id`, `route_id`, `direction_id`, `start_date`, `start_time`, `trip_schedule_relationship` | The trip the vehicle reports. `direction_id`, `start_date` and `start_time` have not been sent so far |
| `lat`, `lon`, `bearing`, `speed`, `odometer` | Position; speed in meters per second |
| `current_stop_sequence`, `stop_id`, `current_status` | The stop the vehicle reports. `current_status` has not been sent so far |
| `congestion_level`, `occupancy_status`, `occupancy_percentage` | As sent |

### `trip-updates-YYYY-MM-DD.parquet`

This table has one row per trip per run.

| Column | Meaning |
|---|---|
| `trip_key` | `trip_id`, or `entity:<id>` if the feed sent no trip id |
| trip columns | As in `vehicles` |
| `vehicle_id` | Vehicle assigned to the trip. The feed also lists upcoming trips that have no vehicle yet |
| `trip_update_ts` | Time of the update |
| `trip_delay` | Trip-level delay in seconds, if sent |
| `stop_time_update_count` | Number of stops with a prediction |

### `trip-update-stops-YYYY-MM-DD.parquet`

This table has one row per predicted stop per run. Each row is the
prediction for one stop on one trip, for as long as that prediction stayed
the same.

| Column | Meaning |
|---|---|
| `trip_key`, trip columns, `vehicle_id`, `trip_update_ts`, `trip_delay` | As in `trip-updates` |
| `stop_sequence`, `stop_id` | The stop |
| `arrival_time`, `departure_time` | Predicted times |
| `arrival_delay`, `departure_delay`, `arrival_uncertainty`, `departure_uncertainty` | In seconds, if sent |
| `stop_schedule_relationship` | As sent |

### `alerts-YYYY-MM-DD.parquet`

This table has one row per alert per run, identified by the alert's
`entity_id`.

| Column | Meaning |
|---|---|
| `entity_id` | The alert's id in the feed |
| `active_periods` | List of start and end times |
| `cause`, `effect` | As sent |
| `header`, `description`, `url` | English text (or the first translation) |
| `route_ids`, `stop_ids`, `trip_ids` | What the alert applies to |

## `timepoints/<passio version>__timetable-<date>-<hash>/`

`pipeline/gtfs/timepoints.py` builds one of these folders for each pairing of
a Passio version with the printed timetable in effect. In the folder name,
`<date>` is the date the timetable took effect, and `<hash>` is a fingerprint
of the reference files and the matching code, so a change to either one
produces a new folder.

The printed timetables are Island Transit's service guides, and the stops
they print times for are the published timepoints. We keep them in
`reference/` (its README explains how). Our code matches each printed stop
name to a Passio stop on the same route, either because the names are
similar or because a person reviewed the match and wrote it down as an
override. When a name fits more than one stop, or none at all, we leave it
unresolved instead of guessing.

| File | Contents |
|---|---|
| `timepoints.csv` | One row per printed stop and matched Passio stop: route, day type, direction, printed order and name, `passio_route_id`, `passio_direction_id`, `passio_stop_id`, `passio_stop_name`, and `match` (`name <score>`, `override`, or the reason it is unresolved) |
| `summary.json` | Reference date, printed names, resolved names, the unresolved names with reasons, the number of timepoint route-stop pairs, and (since `timepoints/0.2.0`) the number of route, direction and stop triples |

| `timepoints.csv` column | Meaning |
|---|---|
| `route_short_name` | The route number as printed in the guide |
| `day_type` | The kind of day the printed timetable is for: `weekday`, `weekday AM`, `weekday PM`, `weekday AM and PM`, `saturday` or `weekend` |
| `direction` | The direction as printed, such as `northbound` or `loop` |
| `order` | Position of the stop in the printed timetable, counting from 1 |
| `printed_stop` | The stop's name as printed |
| `passio_route_id`, `passio_stop_id`, `passio_stop_name` | The Passio route and stop the printed stop was matched to. The stop id and name are empty when the printed name is not a stop |
| `passio_direction_id` | The Passio direction (`direction_id` in Passio's `trips.txt`) whose trips run the way the printed timetable reads. The stop counts as a timepoint only for trips going that direction. Since `timepoints/0.2.0`; lists made before have no such column |
| `match` | How the match was made: `name` and a similarity score from 0 to 1, `override` for a match a person reviewed, or the reason there is none |

Where a route prints separate timetables for different kinds of day (the
`day_type` column: weekday, Saturday and so on), a service day uses only the
rows for its own kind of day. A route that prints nothing for that kind of
day uses all of its rows.

## Stop events

Each night `pipeline/events/infer.py` builds these files, using the method
in `docs/methods.md`, section 3, and stores them in the `parsed-YYYY-MM`
release.

### `stop-events-YYYY-MM-DD.parquet`

This table has one row for every scheduled stop of every trip in that day's
Passio schedule, whether or not we observed it.

| Column | Meaning |
|---|---|
| `trip_id`, `route_id`, `stop_sequence`, `stop_id` | Passio ids |
| `vehicle_key` | The vehicle whose reports were used; empty if none carried the trip id |
| `is_first_stop`, `is_last_stop` | Position in the trip |
| `is_timepoint` | A true timepoint (`timepoints/`): the stop is printed in Island Transit's timetable for this route and, since `events/0.3.2`, for this trip's direction |
| `stop_distance_m` | The stop's distance along the trip's shape; empty if the stop is `unplaced`. Where two points on one stretch of the shape are equally near the stop, the earlier one (since `events/0.3.1`) |
| `stop_offset_m` | How far the stop sits from the shape at that point, in meters (since `events/0.3.0`) |
| `scheduled_arrival`, `scheduled_departure` | Passio schedule, as UTC timestamps |
| `arrival_time` | When the bus first came within 40 meters (about 130 feet) of the stop along the shape |
| `departure_time` | When it moved more than 40 meters past the stop |
| `arrival_gap_s`, `departure_gap_s` | Seconds between the two position reports the time was interpolated from |
| `arrival_status`, `departure_status` | `observed`; `gap` (reports more than 120 s apart, no time given); `not_reached` (tracking ended before the stop); `past_when_first_seen`; `skipped` (the bus left out the part of the shape the stop is on); `no_shape` (the trip has no shape); `unplaced` (the stop could not be placed on the shape in stop order within 150 m of it); `no_reports` (no vehicle carried the trip id); `not_applicable` (no arrival at the first stop, no departure at the last) |

### `trip-tracks-YYYY-MM-DD.parquet`

This table has one row per scheduled trip. It shows which vehicles carried
the trip's id and how many of their position reports we used. It also shows
why we set the rest aside: they came before the trip started
(`reports_before_start`) or after it ended (`reports_after_end`), they were
more than 100 meters (about 330 feet) from the route's shape (`off_shape_reports`), or the bus was
moving the wrong way (`wrong_direction_reports`). Finally, it shows how far
along the shape we followed the bus and which parts of the shape it left out
(`shape_skips`, `skipped_m`). We measure `shape_length_m` after collapsing
retraced segments (`docs/methods.md`, section 3).

| Column | Meaning |
|---|---|
| `trip_id`, `route_id` | Passio ids |
| `scheduled_start`, `scheduled_end` | First scheduled departure and last scheduled arrival, UTC |
| `vehicles_with_trip_id` | Vehicles that carried the trip's id within 30 minutes of its schedule |
| `vehicle_key` | The one whose reports were used (the one with the most) |
| `position_reports` | That vehicle's reports in the window |
| `glitch_reports` | That vehicle's reports in the window that were left out as GPS glitches (`pipeline/glitch.py`, `docs/methods.md` section 3). `position_reports` does not include them. Since `events/0.4.0` |
| `reports_used` | Reports placed on the shape |
| `reports_before_start`, `reports_after_end` | Reports from before tracking began or after the bus reached the end; never compared with the shape |
| `off_shape_reports`, `wrong_direction_reports` | Reports set aside while tracking. Since `events/0.3.1`, a report equally near two segments of the shape is measured on the earlier one, and one nearest a corner has its direction checked against the segment leaving the corner, so the counts are the same on any computer (`docs/methods.md`, section 3) |
| `shape_id`, `shape_length_m` | The trip's shape and its length |
| `max_distance_m` | How far along the shape the bus was followed |
| `shape_skips`, `skipped_m` | Stretches of the shape the bus left out, and their total length |
| `problem` | Empty, or why the trip could not be read from the schedule files (since `events/0.3.0`); such a trip has no stop events |

## `ontime/`

`pipeline/events/ontime.py` writes `ontime/days/YYYY-MM-DD.json` and
`ontime/daily.csv`, following `docs/methods.md`, section 4. The counts are
of scheduled timepoint events, meaning a trip's scheduled time at one of its
true timepoints. We always report `observed_share` next to the on-time
share, and we never count an event as late just because we have no time for
it. Events scheduled before collection began are left out.

Each day file breaks the counts down by route and by hour in Pacific time.
Since `ontime/0.2.0` it also breaks them down by route and timepoint stop
(`by_route_stop`), with the stop's name, its median stop sequence and every
deviation in whole seconds (`deviations_s`). The `validation` section holds
the automatic checks of the method (`pipeline/events/validate.py`,
`docs/methods.md`, section 5). It shows how often our departures agree with
the feed's `current_stop_sequence`, overall and by route, and how many
travel times between stops run backwards or go faster than 35 m/s (about 78
mph), with the fastest listed. Each of those gives the distance in `meters`
(named `metres` in files written before `validate/0.2.0`, November 22,
2026). `daily.csv` carries the agreement share as `feed_agreement_share`.

| `daily.csv` column | Meaning |
|---|---|
| `service_date` | The service day |
| `scheduled` | Scheduled timepoint events |
| `observed`, `observed_share` | Those we could put a time on, and their share of `scheduled` |
| `on_time`, `early`, `late` | Observed events from 1 minute early to 5 minutes late, earlier, and later |
| `on_time_share_of_observed` | `on_time` divided by `observed` |
| `deviation_s_median` | Median of measured minus scheduled time, seconds; positive is late |
| `feed_agreement_share` | Share of inferred departures that agree with the feed's change of stop (check 2) |
| `service_expected` | False on a holiday with no service (`reference/no-service-days.csv`) |
| `ontime_version` | Version of the on-time code |

Every median here is the middle value, or the average of the two middle
values when the count is even (since `ontime/0.3.0`; before that it was the
upper of the two).

`ontime/field-check.json`, written by `pipeline/events/field.py`, holds the
field check (`docs/methods.md`, section 5, check 1). Its `overall`,
`arrival`, `departure`, `pass` and `by_stop` sections each give the number
of timed moments scored, the bias, and the median and mean absolute
difference, all as our time minus the timed one in seconds, along with the
shares within 30 and 60 seconds. The `pass` section covers moments when the
bus was level with the stop, compared with the midpoint of our arrival and
departure. `meets_target` compares the mean absolute difference with 30
seconds once at least `target_min_n` moments (five) have been timed, and is
empty before that. `pairs` lists every comparison. We count separately any rows
whose day hasn't been processed yet, that match no scheduled event, or whose
moment has no inferred time.

`ontime/routes.csv`, from `pipeline/events/routes.py`, has one row per
service day and route.

| `routes.csv` column | Meaning |
|---|---|
| `service_date` | The service day |
| `route_id` | Passio's id for the route |
| `route_name` | The route's number, or its name when it has no number |
| `scheduled`, `observed`, `on_time`, `early`, `late` | Counts of the route's timepoint events that day, as in `daily.csv` |
| `observed_share`, `on_time_share_of_observed` | `observed` divided by `scheduled`, and `on_time` divided by `observed` |
| `deviation_s_median` | Median of measured minus scheduled time, seconds; positive is late |

Shares in the `ontime/` files are rounded to four decimal places, and
shares in the `quality/` files and the monthly reports to six. Every share
is a fraction from 0 to 1, not a percentage.

`ontime/recent.json` pools the last 28 calendar days, overall, by route, and
by route and timepoint stop. It adds up the counts, recomputes the shares
from those sums, and gives the median and quartiles of the deviations, along
with each route's daily on-time and observed shares. Days with no service
are left out and listed in `days_without_service`. Since `routes/0.2.0`
(`routes_version`) it pools only the days measured against the printed
timetable in effect on the last day (`timetable_reference`). When that
leaves out earlier days of the 28, `schedule_change` gives the date the new
timetable took effect (`effective`), how many days were left out
(`days_left_out`) and the last of them (`last_day_left_out`); otherwise it
is empty. One limit remains: if Passio gave its stops new ids without a new
printed timetable, a route's stop list could show the old and the new id
for up to 28 days. The site's route pages read from this file.

## `maps/<passio version>__timetable-<date>-<hash>.json`

`pipeline/gtfs/routemap.py` writes one of these for each timepoints folder.
For each route it records the id, the short and long names, Passio's color
for the route, the number of trips, and the route's paths. Each path is a
Passio shape with any stretch drawn forward, back and forward again
collapsed, simplified so it stays within 5 meters (about 16 feet) of the original, and stored as
`[lon, lat]` pairs rounded to 5 decimal places. `timepoint_stops` lists every
printed-timetable stop that was matched to a Passio stop, with its position
and the routes it serves, and `bbox` gives the map's edges as west, south,
east and north.

## `quality/`

`pipeline/quality/metrics.py`, `pipeline/quality/stops.py` and
`pipeline/quality/gaps.py` compute these files for each service day once
its raw file, parsed tables and pinned schedule all exist. The definitions
are in `docs/methods.md`, section 2.

`quality/days/YYYY-MM-DD.json` holds every measure for the day, grouped
under `paradata`, `nonresponse`, `coverage`, `measurement`, `processing` and
`events`. The `events` group holds the measures built on our inferred stop
times, and it is empty when a day has none. The file also records which
schedule we used (`schedule`) and the SHA-256 fingerprints of the input
files (`inputs`). We recompute a day when `QUALITY_VERSION` changes or when
its parsed tables or stop events are rebuilt, or when its schedule version or
timepoint list changes. When many days are waiting, each
nightly run handles new days first and then works back through the rebuilds,
newest first, seven days at a time. Since `quality/0.3.0`, `coverage.trips` lists
every scheduled trip we could have observed, stored as `columns` and
`rows`. Each row gives the `trip_id` and `route_id`, the Pacific hour of the
trip's first departure (`start_hour`), how many scheduled trips were in
service at that moment counting this one (`scheduled_in_service`), and
whether any position carried the trip's id within 30 minutes of its schedule
(`observed`). Since `quality/0.5.0` each row also says whether the feed
marked the trip as canceled (`canceled`), and `coverage` carries the number
of such trips (`canceled_trips`), how they were marked (`canceled_by`, by a
`flag` on the trip or by an `alert`), their ids (`canceled_trip_ids`), how
many of them a bus reported anyway (`canceled_but_observed`), and
`trip_coverage_excluding_canceled`, which is trip coverage with the canceled
trips that no bus reported taken out of the scheduled trips. `trip_coverage`
itself still counts every scheduled trip. Two more counts show whether the
feed uses these signals at all: `stop_updates_flagged_skipped` is the number
of stop predictions marked `SKIPPED`, and `alerts_seen` is the number of
alert rows that day. Under `paradata.tripUpdates.refresh` we record how
often trip updates really change. A pair is two consecutive fetches that
both listed a trip (`pairs`); it `changed` when the trip's update differed
between them. `update_interval_s_median` and `update_interval_s_p90` are the
time between the feed's own timestamps on two successive updates of a trip.

`quality/daily.csv` has one row per day with the headline figures, rebuilt
from the day files.

`quality/gaps.json`, from `pipeline/quality/gaps.py`, looks at whether the
missed trips happen at random, using every day that has trip rows. Its
`rates` section gives scheduled and unobserved trips by hour, hour band,
route, day type and number of trips in service. Its `model` section is a
logistic regression of "trip not observed," fitted with Firth's penalized
likelihood, with odds ratios, 95% intervals, p-values and a likelihood-ratio
test of all the predictors together. It stays `null` until there are 14
days and 30 unobserved trips, and `model_status` says which of those is
still short; the two numbers are also given as `thresholds`. Since
`gaps/0.3.0` a trip the feed marked as canceled, and that no bus reported, is
left out, because there was nothing to report; `canceled_left_out` counts
them. `limits` states what the model assumes: it treats trips as
independent, so its p-values are more confident than they should be. Since
`gaps/0.4.0` (November 22, 2026) the names inside `model` have no spaces:
`reference_levels` has `hour_band`, `route`, `day_type`,
`trips_in_service_centered_at` and `routes_pooled_as_other`, and each term
is named `hour_band: <band>`, `route: <route id>`, `day_type: <type>` or
`trips_in_service (per trip, centered)`. The figures are unchanged.

| `daily.csv` column | Meaning |
|---|---|
| `service_date` | The service day |
| `service_expected` | False on a holiday with no service (`reference/no-service-days.csv`) |
| `passio_version` | Static version the day was measured against |
| `scheduled_trips` | Passio trips scheduled that day (on the first day, only those still running when collection began) |
| `trip_coverage` | Share of those trips with at least one vehicle position within 30 minutes of the trip's schedule |
| `vehicle_uptime`, `trip_update_uptime` | Share of 20-second slots with a successful fetch. Slots are centered on the moment the scheduler usually fires |
| `vehicle_fetch_failure_rate` | Share of vehicle-position fetches that failed |
| `empty_in_service_rate` | Successful vehicle-position fetches with no vehicles, while a trip was scheduled |
| `stale_in_service_rate` | Successful vehicle-position fetches that listed vehicles but whose newest vehicle timestamp was over 120 s old, while a trip was scheduled. An empty fetch is counted as empty, not stale |
| `longest_gap_s_median` | Median over observed trips of the longest interval between new position reports from the trip's main vehicle, within 30 minutes of its schedule |
| `position_age_s_median`, `position_age_s_p95` | Time from the vehicle's timestamp to the first fetch that returned it; each position counted once |
| `impossible_jumps` | Consecutive reports of one vehicle implying more than 130 km/h (about 80 mph) |
| `timepoint_coverage` | Scheduled timepoint events with an inferred time |
| `stop_coverage` | The same at every stop |
| `off_shape_share` | Position reports more than 100 meters (about 330 feet) from the trip's shape, divided by the reports tracked along it |
| `prediction_mae_s_5min` | Mean absolute error of arrival predictions published 5 minutes ahead, seconds |
| `prediction_median_abs_s_5min` | The typical miss of those predictions: the median absolute error, seconds |
| `prediction_over_30min_share_5min` | Share of those predictions off by more than 30 minutes |
| `prediction_within_60s_5min` | Share of those predictions within 60 s |
| `jitter_m_p95` | 95th percentile of position spread while a bus stands at a terminal, meters |
| `unassignable_trip_ids` | Trip ids reported that are not in the static version or not scheduled that day |
| `vehicles_missing_vs_30d` | Vehicles seen in the previous 30 days (with quality files) but not this day |
| `trip_id_match_rate` | Positions whose `trip_id` exists in the static version |
| `over_coverage_share` | Positions with no trip, an unknown trip, or a trip not scheduled that day |
| `vehicle_latency_ms_median` | Median response time of the vehicle-position feed |
| `vehicles_seen` | Distinct vehicles reporting |
| `timetable_reference` | Effective date of the printed timetable used for timepoints |
| `timepoint_names_resolved_share` | Printed timepoint names tied to a Passio stop |
| `canceled_trips` | Scheduled trips the feed marked as canceled, by a flag on the trip or by a `NO_SERVICE` alert naming it |
| `trip_coverage_excluding_canceled` | Trip coverage with the canceled trips that no bus reported taken out of the scheduled trips. Equal to `trip_coverage` when nothing was canceled |
| `trip_update_changed_share` | Of the pairs of consecutive fetches that both listed a trip, the share in which the trip's update had changed |
| `trip_update_interval_s_median` | Typical time between the feed's timestamps on two successive updates of a trip, seconds |
| `passio_basis` | How the day's Passio version was chosen (see `manifest/static.json`) |
| `passio_changed_during_day` | True when complete Passio downloads on that day differed, which marks a schedule change |
| `glitch_reports_removed` | Position reports left out as GPS glitches: a bus jumping somewhere impossible and back (`measurement.glitches` in the day file). Since `quality/0.6.0` |
| `unresolved_jumps` | Impossible jumps that were not left out, because there was no jump back or the bus could have made it by driving fast (often a timestamp a few seconds off). Since `quality/0.6.0` |
| `quality_version` | Version of the quality code |

Counts of positions (`over_coverage_share`, `trip_id_match_rate`, the share
of positions missing each field) count each position once, by vehicle and
vehicle timestamp. The parsed tables can hold the same position in two rows
when a failed fetch falls between two fetches that returned it.

## `reports/`

`pipeline/monthly.py` writes a report for each month once its last day has
been archived and processed, or seven days after the month ended, whichever
comes first. It rebuilds a report whenever one of its inputs changes: a
figure, a file or a code version behind it. A day that is rebuilt to the
same result does not count: since `monthly/0.2.0` the times and commit ids
stamped on the inputs (`computed_at`, `built_at`, `archived_at`, `code_sha`)
are left out of `inputs_digest`.

`reports/YYYY-MM.json` starts with `period`, which gives the days archived,
the days missing since collection began (`days_missing`), and the partial
days on which some scheduled trips ended before collection started
(`partial_days`). `collection` gives, for each feed, the fetch slots
expected and successful, the pooled uptime, the failure rate and the daily
range. `coverage` gives the pooled trip coverage and its daily range.
`ontime` sums the counts over the month and gives the shares of observed
events, overall and by route, along with the daily ranges of the on-time
share and of the median deviation. `daily_headline` gives the minimum,
median and maximum over the month of every numeric column in
`quality/daily.csv`. `releases` gives the files and bytes in each release,
plus the name, size and SHA-256 of its checksum file. `versions` lists the
collector, tables, stop-event, quality and on-time versions used, and
`inputs_digest` fingerprints everything the report read.

`reports/YYYY-MM.md` says the same thing in plain text and also serves as
the notes on that month's Releases. `reports/index.json` has one row per
month with the headline figures: `month`, `days_archived`,
`days_missing_count` (how many days are in the report's `days_missing`
list), `vehicle_uptime`, `trip_coverage`, `timepoint_observed_share`,
`on_time_share_of_observed`, `bytes` and `computed_at`. In a report's
`ontime.by_route`, a route without a number has an empty `short_name`, as in
the on-time files.

Each Release also gets a `SHA256SUMS-<release>.txt` file. It lists
`<sha256>  <file name>` for every file the manifests record in that release,
in the format `sha256sum -c` can check.

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

## `manifest/events.json`

| Field | Meaning |
|---|---|
| `version`, `repo` | Format version of the manifest and the repository it belongs to, as in `manifest/raw.json` (`repo` was added on November 22, 2026) |
| `days.<date>.events_version`, `ontime_version`, `validate_version` | Versions of the code behind the stop events, the on-time summary and the automatic checks. A day is rebuilt when any of them changes |
| `days.<date>.service_expected` | False on a holiday with no service |
| `days.<date>.passio_version`, `timepoints` | The schedule version and timepoints folder used |
| `days.<date>.vehicles_sha256` | SHA-256 of the vehicles table the events were built from. A day is rebuilt when that table is |
| `days.<date>.rows` | Rows in `stop-events` and `trip-tracks` |
| `days.<date>.files`, `release_tag`, `code_sha` | As in `manifest/raw.json` |

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

`pipeline/gtfs/versions.py` builds this file from the schedule downloads in
the raw archive.

We identify a version of the schedule by the SHA-256 fingerprints of every
file in the zip except `feed_info.txt`. We leave that file out because its
dates change on their own, rolling forward each day in Passio's case and
recording when the file was generated in Trillium's. A version's id is
`<source>-<first 12 hex digits>`. A download counts as complete when the zip
is intact and has every required GTFS file, and only complete downloads
define versions.

| Field | Meaning |
|---|---|
| `versions.<id>.source` | `passio` or `trillium` |
| `versions.<id>.files` | SHA-256 of each file (except `feed_info.txt`) |
| `versions.<id>.feed_info` | `feed_info.txt` of the first download of this version |
| `versions.<id>.first_seen`, `last_seen`, `first_day` | When this version was downloaded |
| `versions.<id>.example` | A release file holding this version |
| `versions.<id>.downloads` | Ids of the complete downloads with this content |
| `downloads.<id>` | Each download: day, time, release file, `complete`, `problems`, `missing` files, and `version` (empty if incomplete) |
| `days.<date>.<source>.version` | The version pinned to that service day. Once set it does not change, except by an override recorded in `override`. It is empty while a day whose downloads disagree waits for its parsed tables |
| `days.<date>.<source>.basis` | Which rule chose it: `complete download that day` (the first one); `incomplete download that day, matched to a complete version` (every file it had was identical to that version's); `carried forward from <date>` (the latest earlier complete download); `no complete version yet`; `complete downloads that day disagreed; chosen by match to the live feed`; `override: <reason>` |
| `days.<date>.<source>.changed_during_day` | True when downloads on that day disagree |
| `days.<date>.<source>.candidates` | Only when complete downloads on that day disagreed: one entry per version, in download order, with the number of trips that version schedules for the day (`scheduled_trips`), how many of them the live feed reported (`scheduled_trips_seen`), how many trip ids the live feed reported (`live_trip_ids`) and how many of those the version contains (`live_trip_ids_in_version`) |
| `days.<date>.<source>.chosen_by` | Which of those figures decided: `scheduled trips seen in the live feed`, `live trip ids known to the version`, or `tie: first complete download that day` |
| `days.<date>.<source>.override` | Only when a row of `reference/static-pin-overrides.csv` replaced the pin: `reason`, `reviewed_on`, and the version and basis it replaced |
| `days.<date>.<source>.pin_version` | Version of the pinning rules, written on pins made by the live-feed rule or by an override (`pin/0.2.0`) |
| `days.<date>.crosswalk` | Folder under `crosswalk/` for that day's pair of versions |
| `days.<date>.timepoints` | Folder under `timepoints/` for that day's Passio version and printed timetable |

## `crosswalk/<passio version>__<trillium version>/`

`pipeline/gtfs/crosswalk.py` builds one of these folders for each pair of
pinned versions. The real-time feed uses Passio's ids, so the crosswalk is
how you get from Passio to Trillium. We keep Trillium's timepoint flags for
comparison only, and the true timepoints are in `timepoints/`.

| File | One row per | Contents |
|---|---|---|
| `stops.csv` | Passio stop | Matched Trillium stop, `method` (`stop_code`: same code within 150 meters, about 490 feet; `distance`: nearest Trillium stop within 60 meters, about 200 feet; empty: unmatched), distance, and the nearest Trillium stop either way |
| `routes.csv` | Passio route | Matched Trillium route, by short name. Passio's three NAS Whidbey routes have no short name and map to Trillium's single NAS route |
| `timepoint_stops.csv` | Passio route and stop | `trillium_timepoint` (`true` or `false` since `crosswalk/0.2.0`): Trillium marks the matched stop as a timepoint on at least one trip of the matched route. Also the counts of Passio stop times, Trillium stop times and Trillium timepoint stop times behind that decision |
| `schedule_agreement.csv` | Passio stop time at a Trillium timepoint | `nearest_trillium_minus_passio_s`: seconds from the Passio time to the nearest Trillium timepoint time at that stop and route, on a day of the week both serve. Empty if Trillium has none |
| `summary.json` | | Counts of the above |

| `stops.csv` column | Meaning |
|---|---|
| `passio_stop_id`, `passio_stop_code`, `passio_stop_name` | The Passio stop |
| `trillium_stop_id`, `trillium_stop_code`, `trillium_stop_name` | The matched Trillium stop. Empty when there is no match |
| `method` | `stop_code`, `distance`, or empty for no match |
| `distance_m` | Meters from the Passio stop to the matched stop, or to the nearest one when there is no match |
| `nearest_trillium_stop_id`, `nearest_trillium_stop_name`, `nearest_distance_m` | The nearest Trillium stop, matched or not, and its distance in meters |

| `routes.csv` column | Meaning |
|---|---|
| `passio_route_id`, `passio_route_short_name`, `passio_route_long_name` | The Passio route |
| `trillium_route_id`, `trillium_route_long_name` | The matched Trillium route |
| `method` | `route_short_name` when the two share a route number, or `no short name in either feed` for the NAS Whidbey routes |

| `timepoint_stops.csv` column | Meaning |
|---|---|
| `passio_route_id`, `passio_stop_id` | The Passio route and stop |
| `trillium_route_id`, `trillium_stop_id` | The matched Trillium route and stop. The stop is empty when it has no match |
| `passio_stop_times` | Passio stop times at this stop on this route |
| `trillium_stop_times`, `trillium_timepoint_stop_times` | Trillium stop times at the matched stop on the matched route, and how many of them Trillium marks as timepoints |
| `trillium_timepoint` | `true` when Trillium marks at least one of them as a timepoint |

| `schedule_agreement.csv` column | Meaning |
|---|---|
| `passio_trip_id`, `passio_route_id`, `passio_stop_id` | The Passio trip, route and stop of the stop time |
| `stop_sequence` | The stop's position in the trip |
| `passio_time` | The scheduled time as written in the schedule file: a local Pacific clock time (`HH:MM:SS`), not a UTC timestamp |
| `nearest_trillium_minus_passio_s` | Seconds from the Passio time to the nearest Trillium timepoint time. Empty if Trillium has none |

## The collector's live tables

Anyone can read the collector's live tables with the public key in
`.env.example`, until a day has been archived and pruned. `fetch_log` is
kept permanently, while `raw_export` and `static_export` keep only recent
days. There is also the `status_now` view, the `prune_log` table, which
records each run of the job that clears archived days, and two functions,
`day_digest(date)` and `collector_health()`. The full list is in
`docs/collector-setup.md`.
