# Methods

We wrote these definitions down before writing the code that computes them,
and `docs/decisions.md` records every change made since.

We store all times in UTC. A service day is a calendar date in Pacific time
(`America/Los_Angeles`), which works neatly because Island Transit runs no
trips across midnight. When we say "scheduled," we mean the Passio static
GTFS version in effect that day. If Passio's copy is missing its service
calendar, we take the calendar from the Trillium feed instead.

## 1. Collection

The collector asks for vehicle positions and trip updates every 20 seconds
and for service alerts every 5 minutes. It does this around the clock, even
in the hours when no buses run.

The collector saves each response exactly as it arrived, before anything
reads it, along with a SHA-256 hash. A hash is a long fingerprint of the file's
contents, and it lets anyone confirm later that the stored copy has not
changed by a single byte.

The collector logs every attempt, including the ones that fail. Each log
entry records when the request was scheduled, when it went out, when the
answer came back, the HTTP status, the size of the response, and any error.
The collector does not retry within a 20-second tick. If more than three
ticks in a row fail, it backs off and waits 40 seconds, then 80 seconds, and
so on up to 10 minutes, and it logs each tick it skips as skipped.

## 2. Feed-quality metrics

We compute these measures for each service day, and also by route and by
hour where that makes sense. The code is in `pipeline/quality/metrics.py`,
and a few details of how it works matter for reading the numbers.

Passio's calendar has no holiday exceptions, so a holiday looks like a
normal service day in our "scheduled" counts. To make that visible, each
day's figures sit next to the list of trips the Trillium feed removes for
holidays that day (`schedule.trillium_removes_service_today`). On the first
day of collection, we counted only the trips that had not already ended when
collection began.

We measure position age at the first fetch that returned a given position.
The measures that depend on our inferred stop times (stop-level coverage,
positions that stray off the route, and prediction error) live in
`pipeline/quality/stops.py` and appear under `events` in each day's file.
Stop-level coverage uses the same moment as the on-time figures: the
departure, or the arrival at a trip's last stop. Off-route counts include
only reports made during a trip's scheduled window, taken from
`trip-tracks`.

To measure prediction error, we take each stop time we observed and compare
it with the prediction the feed was publishing a given number of minutes
earlier. A prediction counts as still published until 30 seconds after the
last fetch that returned it. The feed sends no `start_date`, and on
2026-11-19 about 1 to 2% of the predictions made 1 to 5 minutes ahead carried
a clock time on the next day. We count those as "dated another day" and leave
them out of the error figures.

Jitter tells us how much a parked bus's GPS position wanders. We look for a
dwell, which is 3 or more reports in a row from one vehicle, each moving
slower than 0.5 m/s, within 150 m of a trip's first or last stop, and no more
than 120 seconds apart. Jitter is the distance of each report from the
middle (median) position of its dwell. Speed is whatever the vehicle itself
reports.

The count of vehicles missing compared with the previous 30 days uses only
days that have a quality file, so it is empty on the first day. We publish
the randomness-of-gaps rates from the first day, but the model behind them
waits until there are 14 days and 30 trips that never showed up.

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

The feed tells us where a bus is, but never when it reached a stop. We work
out stop times with the standard approach for this kind of vehicle-location
data, in five steps.

First, our script places each stop on the trip's shape, the line the route
follows on a map. A shape can pass the same stop more than once, on loops or
out-and-back roads, so every pass within 150 m counts as a candidate, and
the script picks the placements that keep the stops in order while keeping
them as close to the shape as possible overall. Before doing that, it
collapses any stretch that a shape draws forward, back and forward again.
Passio's route 1C shapes repeat every segment this way, which made them
about 21 km too long, and we report the metres removed as a processing
error.

Second, the script projects each bus position onto the shape to find how far
along the route the bus has traveled. Tracking starts at the first position
within 300 m of the start of the shape, because a bus often shows its next
trip's id while it is still finishing the last one. If there is no such
position, tracking starts 2 minutes before the scheduled departure. It stops
when the bus reaches the end of the shape.

Third, the script insists that the bus keep moving forward, so it searches
only a window ahead of the last distance it matched. That is how it copes
with loops and with stops the route visits twice. Sometimes two positions in
a row land on a later part of the shape, within 30 m of it and heading
within 45 degrees of the shape's direction there. When that happens, the
script concludes that the bus left out part of the published route, such as
a loop it only drives on request, and moves tracking forward. Stops in the
stretch the bus left out get no time, and the script marks them `skipped`.

Fourth, for each stop the script estimates the moment the bus crossed it by
interpolating, that is, by drawing a straight line in time between the
positions just before and just after the stop.

Fifth, it turns those crossings into an arrival and a departure. The arrival
is the first moment the bus is within 40 m of the stop along the shape, and
the departure is the last, the moment the bus gets more than 40 m past it.
The script never lets distance along the shape go backwards, so GPS wobble
cannot make a bus seem to cross a stop twice. It also sets aside, and
counts, any position more than 100 m from the shape or moving against the
route's direction faster than 3 m/s.

The code for all of this is in `pipeline/events/infer.py`. For the on-time
figures we use only the timepoints printed in Island Transit's timetables
(`pipeline/gtfs/timepoints.py`).

Each inferred stop time remembers how far apart in time its two positions
were. If they were more than 120 seconds apart, we don't trust a straight
line across the gap, and we record the stop as not observed.

The table below lists the problems we know about and what we do about each
one.

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

A bus's schedule deviation is how far its actual departure from a timepoint
was from the scheduled departure. At the last stop of a trip we use the
arrival instead.

A departure counts as on time if it falls anywhere from 60 seconds early to
300 seconds late, with both ends included. An early arrival at the last stop
also counts as on time, because a bus that reaches the end of its line early
has not made anyone miss it. Anything outside that window is early or late.

When we could not work out a time for a scheduled timepoint, we call it not
observed. We report those separately and never count them as late. The feed
cannot tell a canceled trip apart from one that ran without reporting,
unless an alert or a `CANCELED` flag says so, which is why we always show
the on-time figures next to the share of scheduled departures we actually
observed.

## 5. Validation

Three checks back up the inferred times and the on-time figures.

Check 1 is the field check. Someone times arrivals and departures in person,
using a clock synced to the network, and we compare those times with ours.
We report the bias (whether our times tend to run early or late), the mean
absolute error (the average size of the difference), and the share within 30
and 60 seconds. The target is a mean absolute error of 30 seconds or less.
It is a spot check of whether the tracking matches what happens on the
street, and it has been done on route 60, the route the observer rides. So
it does not yet cover other routes, departures after a bus has waited at a
timepoint, or departures from a terminal. Checks 2 and 3 cover every route
every day.

Check 2 asks whether our stop times agree with the stop sequence the feed
itself reports (`current_stop_sequence`). Check 3 asks whether any travel
time between stops runs backwards or implies an impossible speed without a
good explanation.

Every night, `pipeline/events/field.py` scores check 1 from
`reference/field-observations.csv` and writes the result to
`ontime/field-check.json`. The observer times the same moments our method
looks for: the bus coming within 40 m of the stop, and the bus getting more
than 40 m past it (`docs/field-check.md`). Riding on board, it is often
easier to note the moment the bus is level with the stop, and we compare
that with the midpoint of our inferred arrival and departure. The script
matches each timed moment to our stop time for the same service date, trip
and stop, and each difference is our time minus the timed one. When we have
no inferred time for a timed moment, we count it but don't score it.

Checks 2 and 3 also run every night (`pipeline/events/validate.py`), and
their results go into `validation` in `ontime/days/<date>.json`.

For check 2, it helps to know that `current_stop_sequence` names the stop a
bus is heading for. When the bus passes stop k, that number changes to k + 1
somewhere between the last report showing k or less and the first report
showing more. Our departure time agrees with the feed if it falls inside
that interval, widened by 30 seconds on each side. We leave out first and
last stops. On 2026-11-19, 95% of 2,902 inferred departures agreed, and the
first report naming the next stop came a median of 3.8 seconds after our
departure time.

For check 3, our times along a trip, from the departure at one stop to the
arrival at the next, must never run backwards or imply a speed above
35 m/s. We don't measure segments across a stretch the bus left out. On
2026-11-19 none ran backwards, and 29 of 5,919 went faster than 35 m/s. Every
one of those fell within a single 16 to 40 second gap between reports on a
fast road, where the projection can jump ahead as far as the 35 m/s search
limit allows. The day file lists the fastest ones.
