# Methods

Draft. Definitions are fixed here before the code that computes them is
written; changes are recorded in `docs/decisions.md`.

All times are stored in UTC. A service day is the calendar date in
`America/Los_Angeles`; Island Transit schedules no trips across midnight.
"Scheduled" means the Passio static GTFS version in effect on that day, with
the service calendar taken from the Trillium feed when Passio's is missing.

## 1. Collection

- Vehicle positions and trip updates are requested every 20 seconds, service
  alerts every 5 minutes, all day, including hours with no service.
- Each response is stored exactly as received, with a SHA-256 hash, before
  anything is parsed.
- Every attempt is logged: scheduled time, request and response times,
  HTTP status, size, and any error. Failed attempts are logged too.
- No retries within a tick. After more than three consecutive failing ticks
  the collector backs off (40 s, 80 s, … up to 10 minutes) and logs each
  skipped tick as skipped.

## 2. Feed-quality metrics

Computed per service day, and by route and hour where that applies.

Implemented in `pipeline/quality/metrics.py`. Notes on the implementation:

- "Scheduled" uses the Passio calendar. It has no holiday exceptions; the
  Trillium feed's holiday removals for the day are reported next to the
  figures (`schedule.trillium_removes_service_today`).
- On the first day of collection only trips that had not ended when
  collection began count as scheduled.
- Position age is measured at the first fetch that returned a position.
- Metrics built on inferred stop events (stop-level coverage, off-shape
  positions, prediction error) are in `pipeline/quality/stops.py`, grouped
  under `events` in the day file, and labelled experimental like the events.
- Stop-level coverage uses the same measured time as the on-time figures:
  departure, or arrival at a trip's last stop. Off-shape counts are of
  reports within a trip's scheduled window, from `trip-tracks`.
- Prediction error compares each observed stop event with the prediction
  being published the given number of minutes before it (a prediction
  counts until 30 s after the last fetch that returned it). The feed sends
  no `start_date`, and on 2026-11-19 about 1 to 2% of predictions made 1 to
  5 minutes ahead were clock times on the next day. Those are counted as
  "dated another day" and left out of the error figures.
- Jitter: a dwell is 3 or more consecutive reports of one vehicle, each
  under 0.5 m/s and within 150 m of a trip's first or last stop, at most
  120 s apart; jitter is each report's distance from the dwell's median
  position. Speed is as reported by the vehicle.
- Vehicles missing against the previous 30 days uses the days that have a
  quality file, so it is empty on the first day.
- Randomness of gaps is published from the first day as rates; the model
  is fitted once there are 14 days and 30 unobserved trips.

### Coverage

| Metric | Definition |
|---|---|
| Trip coverage | Scheduled trips with at least one vehicle position carrying that `trip_id`, divided by scheduled trips |
| Stop-level coverage | Scheduled timepoint events with an inferred stop event, divided by scheduled timepoint events |
| Never-reporting units | Routes with scheduled service and no observations; vehicles seen in the previous 30 days but not today |
| Over-coverage | Positions whose `trip_id` is not in the schedule, or that have no `trip_id` |

### Nonresponse

| Metric | Definition |
|---|---|
| Fetch nonresponse | Fetches with an error, timeout, non-200 status or undecodable body, divided by fetches attempted |
| Empty-in-service rate | Fetches with zero vehicles while at least one trip is scheduled, divided by fetches while at least one trip is scheduled |
| Stale feed | Fetches whose newest vehicle timestamp is more than 120 s old while service is scheduled |
| Trip gaps | For each observed trip: the longest interval with no new position, and the share of the trip spent in gaps over 90 s |
| Item nonresponse | Share of positions missing each field |
| Randomness of gaps | Share of scheduled trips with no position report by hour, route, day type and number of trips in service, over all days; a logistic model of "trip not observed" on hour band (before 7, 7–9, 9–15 reference, 15–18, 18 on), route (routes with under 30 trips pooled), day type and the number of scheduled trips in service at the trip's start. Firth's penalised likelihood keeps estimates finite when a route has no unobserved trips. The likelihood-ratio test of all predictors together tests whether gaps are missing completely at random. Fitted from 14 days and 30 unobserved trips |

### Measurement error

| Metric | Definition |
|---|---|
| Position age | Fetch time minus vehicle timestamp: median and 95th percentile |
| Impossible jumps | Consecutive positions implying more than 130 km/h, or lying more than 100 m from the trip's shape |
| Jitter | Displacement while the bus is stationary at a terminal: 95th percentile, metres |
| Clock anomalies | Vehicle timestamps in the future or going backwards |
| Prediction error | Predicted minus inferred actual arrival at 1, 2, 5, 10, 15 and 30 minutes ahead: bias, mean absolute error, share within 60 s. Only if trip updates are published |

### Processing error

| Metric | Definition |
|---|---|
| ID match rate | Positions whose `trip_id`, `route_id` and `stop_id` exist in the static GTFS in effect |
| Unassignable trips | Observed trips that cannot be tied to a scheduled trip on that day |
| Static feed integrity | Each day: is the zip valid, and are the required files present |
| Schedule agreement | Passio stop times at Trillium timepoints whose time matches a Trillium timepoint time at the same stop and route (same time, within 60 s, within 300 s). The two published schedules are not the same; Trillium's is out of date |
| Timepoint resolution | Printed timetable stop names tied to a Passio stop, divided by printed names. Falls if a service change renames or moves stops before the reference is updated |
| Parser rejects | Entities dropped by validation, divided by entities read |

### Paradata

| Metric | Definition |
|---|---|
| Collector uptime | 20-second slots with a logged fetch, divided by slots in the day; split by cause (scheduler did not fire, function failed, feed failed) |
| Latency | Response time: median and 95th percentile |
| Instrument changes | Dated log of static GTFS versions, collector versions and configuration changes |

## 3. Arrival and departure times

The feed reports where a bus is, not when it reached a stop. Stop times are
inferred with the standard approach for vehicle-location data:

1. Place each stop on the trip's shape. A shape can pass a stop more than
   once (loops, out-and-back roads), so every pass within 150 m is a
   candidate, and the placement keeps the stops in order while minimising
   their summed distance from the shape. Before this, segments a shape
   draws forth, back and forth again are collapsed to one (Passio's route 1C
   shapes repeat every segment this way, which made them about 21 km too
   long); the metres removed are reported as a processing error.
2. Project each position onto the trip's shape to get distance along the
   route. Tracking starts at the first position within 300 m of the start of
   the shape, because a bus often shows its next trip id while finishing the
   previous trip; without one, it starts 2 minutes before the scheduled
   departure. It stops when the bus reaches the end of the shape.
3. Require forward progress, searching only a window ahead of the last
   matched distance (this handles loops and repeated stops). If two
   consecutive positions lie on a later part of the shape (within 30 m,
   heading within 45 degrees of the shape's direction there), tracking moves
   forward to it: the bus has left out part of the published shape, for
   example an on-request loop. Stops in the stretch left out get no time
   (status `skipped`).
4. For each stop, interpolate the time the bus crossed the stop's distance
   between the two positions on either side.
5. Arrival is the first time within 40 m of the stop along the shape;
   departure is the last, that is, the moment the bus is more than 40 m past
   it. Distance along the shape is kept non-decreasing so GPS jitter cannot
   produce a second crossing. Positions more than 100 m from the shape, or
   moving against its direction above 3 m/s, are counted and not used.

Implemented in `pipeline/events/infer.py`; the stops used for on-time
figures are the printed timetable timepoints (`pipeline/gtfs/timepoints.py`).

Each inferred event keeps the time gap between the two positions it was
interpolated from. If that gap exceeds 120 s the event is recorded as not
observed.

Known problems and how they are handled:

| Problem | Handling |
|---|---|
| Layovers at terminals | First stop: departure only. Last stop: arrival only |
| Vehicles with no trip | Excluded; counted as over-coverage |
| Trip label changes early or late at a layover | Positions accepted only within the trip's scheduled window ± 30 minutes and on its shape |
| Detours | Events more than 100 m off the shape are flagged and excluded from on-time figures |
| Parts of the shape not driven | Tracking moves past them once two positions agree; their stops are `skipped`, counted as not observed |
| Gaps in positions | No interpolation across more than 120 s |
| Interpolated schedule times | On-time figures use true timepoints only: the stops printed in Island Transit's public timetables for the service period, matched to Passio stops on the same route (`pipeline/gtfs/timepoints.py`, `reference/`, `timepoints/`). Printed names that cannot be tied to one stop are left out and counted. Scheduled times are Passio's, the schedule the real-time feed follows |
| Schedule changes daily | Each service day is pinned to the static version in effect that day |

## 4. On-time performance

- **Schedule deviation**: actual departure minus scheduled departure at a
  timepoint; at the final stop, actual arrival minus scheduled arrival.
- **On time**: deviation from 60 s early to 300 s late, inclusive. Early
  arrival at the final stop counts as on time.
- **Early / late**: outside that window.
- **Not observed**: a scheduled timepoint event with no inferred time. These
  are reported as their own category and are never counted as late. The feed
  cannot distinguish a canceled trip from one that ran without reporting,
  unless an alert or a `CANCELED` flag says so.
- On-time figures are always shown next to the share of scheduled events
  that were observed.

## 5. Validation

On-time figures are labelled experimental until all three hold:

1. Field check: about 40 arrivals and departures timed in person at three or
   four timepoints with a synchronised clock. Reported: bias, mean absolute
   error, share within 30 s and 60 s. Target: mean absolute error of 30 s or
   less.
2. Inferred stop events agree with the feed's own `current_stop_sequence`
   changes.
3. No negative segment travel times or impossible speeds remain unexplained.

Check 1 is scored every night by `pipeline/events/field.py` from
`reference/field-observations.csv` into `ontime/field-check.json`. The
observer times the moments the method infers: the bus coming within 40 m
of the stop and the bus more than 40 m past it (`docs/field-check.md`).
Each timed moment is matched to the inferred event with the same service
date, trip and stop; differences are inferred minus timed. Timed moments
with no inferred time are counted, not scored.

Checks 2 and 3 run every night (`pipeline/events/validate.py`) and are
written to `validation` in `ontime/days/<date>.json`:

- Check 2: the feed's `current_stop_sequence` is the stop the bus is
  heading for. When the bus passes stop k it changes to k + 1, somewhere
  between the last report showing k or less and the first showing more. An
  inferred departure agrees if it lies in that interval, widened by 30 s.
  First and last stops are left out. On 2026-11-19, 95% of 2,902 inferred
  departures agreed; the first report naming the next stop came a median
  3.8 s after the inferred departure.
- Check 3: consecutive inferred times along a trip (departure from one stop,
  arrival at the next) must not run backwards or exceed 35 m/s. Segments
  across a stretch the bus left out are not measured. On 2026-11-19 none ran
  backwards; 29 of 5,919 exceeded 35 m/s, all within single 16 to 40 s
  gaps between reports on fast roads, where the projection can advance up
  to the 35 m/s search limit. The fastest are listed in the day file.
