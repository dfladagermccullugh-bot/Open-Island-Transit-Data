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
- Not yet computed (they need stop events or shapes): stop-level coverage,
  vehicles missing against the previous 30 days, randomness of gaps,
  off-shape positions, jitter, prediction error, unassignable trips.

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
| Randomness of gaps | Gap rates by hour, route and day type; a logistic model of "trip not observed" on hour, route and number of buses scheduled |

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

1. Project each position onto the trip's shape to get distance along the
   route.
2. Require forward progress, searching only a window ahead of the last
   matched distance (this handles loops and repeated stops).
3. For each stop, interpolate the time the bus crossed the stop's distance
   between the two positions on either side.
4. Arrival is the first time within 40 m of the stop along the shape;
   departure is the last.

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
