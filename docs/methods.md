# Methods

We wrote these definitions down before writing the code that computes them,
and our [decisions log](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/blob/main/docs/decisions.md) records every change made since.

Two companies publish Island Transit's data. Passio hosts the live feed and
the current schedule, and Trillium publishes an older copy of the schedule
that is out of date but still lists holiday cancellations. Both use GTFS, the
standard file format transit agencies use to share their timetables, and the
live feed uses its real-time version, GTFS-Realtime.

We store all times in UTC. A service day is a calendar date in Pacific time
(`America/Los_Angeles`), which works neatly because Island Transit runs no
trips across midnight. When we say "scheduled," we mean the Passio schedule
version in effect that day. Passio's download sometimes arrives incomplete,
without its service calendar. When that happens, we use the complete Passio
download that has exactly the same files. On a day when the schedule
changes, the download we make at night can still be the old schedule while
the buses already run on the new one. When two complete downloads on the
same day differ, we count how many of each version's scheduled trips the
live feed reported that day, and we use the version with more. Once a day
has been matched to a schedule version, it keeps that version, so a figure
we have published is never recomputed against a different schedule. The one
exception is a correction we make by hand, which we write down with its
reason in `reference/static-pin-overrides.csv` and record beside the day.

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
so on up to 10 minutes, and it logs each tick it skips as skipped. If the
database refuses to store a tick's responses, the collector tries the write
once more before giving up on that tick.

(Code: `supabase/functions/collect/` and `supabase/migrations/`; setup in
`docs/collector-setup.md`.)

## 2. Feed-quality metrics

We compute these measures for each service day, and also by route and by
hour where that makes sense. Survey researchers sort the ways a source of
data can go wrong into five groups, and we use the same groups below, each
with a plain name beside the researchers' term.

### Trips that show up (coverage)

| Metric | Definition |
|---|---|
| Trip coverage | Scheduled trips with at least one vehicle position carrying that `trip_id` within 30 minutes of the trip's scheduled time, divided by scheduled trips |
| Canceled trips | Scheduled trips the feed itself says did not run: the trip carries a `CANCELED` or `DELETED` flag in a trip update or a vehicle position, or a service alert with the effect `NO_SERVICE` names the trip while the trip is scheduled. Counted beside trip coverage. A second coverage figure takes the canceled trips that no bus reported out of the scheduled trips |
| Stop-level coverage | Scheduled timepoint events with an inferred stop event, divided by scheduled timepoint events |
| Never-reporting units | Routes with scheduled service and no observations; vehicles seen in the previous 30 days but not today |
| Over-coverage | Positions whose `trip_id` is not in the schedule or not scheduled that day, or that have no `trip_id`, divided by positions |

A trip id on its own is not enough to say a trip was tracked. The same ids
run every day, and a bus waiting at a terminal often shows its next trip's
id long before that trip starts. So a trip counts as seen only when a
position carries its id between 30 minutes before its first scheduled
departure and 30 minutes after its last scheduled arrival.

Passio's calendar has no holiday exceptions, so a holiday looks like a
normal service day in our "scheduled" counts. Island Transit's website says
it runs no service on New Year's Day, Memorial Day, Independence Day, Labor
Day, Thanksgiving Day and Christmas Day. We keep those dates in
`reference/no-service-days.csv`, each with the page it came from. A listed
day is archived and measured like any other, and its files carry
`service_expected: false`. Figures that pool several days, which are the
28-day route tables, the randomness-of-gaps rates and model, and the monthly
reports, leave it out, so a holiday does not read as a day of lost buses.
Each day's figures also sit next to the list of trips Trillium removes for
holidays that day (`schedule.trillium_removes_service_today`). On the first
day of collection, we counted only the trips that had not already ended when
collection began.

Stop-level coverage uses the same moment as the on-time figures: the
departure, or the arrival at a trip's last stop. The count of vehicles
missing compared with the previous 30 days uses only days that have a
quality file, so it is empty on the first day.

### Missing data (nonresponse)

| Metric | Definition |
|---|---|
| Fetch nonresponse | Fetches with an error, timeout, non-200 status or undecodable body, divided by fetches attempted |
| Empty-in-service rate | Successful fetches with zero vehicles while at least one trip is scheduled, divided by successful fetches while at least one trip is scheduled |
| Stale feed | Successful fetches that list vehicles but whose newest vehicle timestamp is more than 120 s old, while service is scheduled, divided by the same successful fetches |
| Trip gaps | For each observed trip: the longest interval with no new position, and the share of the trip spent in gaps over 90 s |
| Item nonresponse | Share of positions missing each field |
| Randomness of gaps | Checks whether missed trips happen at random or cluster around certain routes, times of day or busy hours. Rates: the share of scheduled trips with no position report within 30 minutes of their schedule, by hour, route, day type and number of trips in service, over all days. Model: a logistic regression of "trip not observed" on hour band (before 7, 7–9, 9–15 reference, 15–18, 18 on), route (routes with under 30 trips pooled), day type and the number of scheduled trips in service at the trip's start. Firth's penalized likelihood keeps estimates finite when a route has no unobserved trips. The likelihood-ratio test of all predictors together tests whether gaps are missing completely at random. Fitted from 14 days and 30 unobserved trips |

We publish the randomness-of-gaps rates from the first day, but the model
behind them waits until there are 14 days and 30 trips that never showed up.
The model treats every trip as separate. In practice, trips lost on the same
day or by the same bus tend to go missing together, as when one bus's
tracker fails for a whole shift. That makes the model's p-values more
confident than they should be, and we say so wherever it is shown. The rates
do not have this problem.

An empty fetch is counted as empty and not also as stale, because it has no
vehicle timestamp to be old. Trip gaps use the positions of one vehicle, the
one with the most reports for the trip, inside the trip's 30-minute window.
When two buses carry the same trip id, mixing their reports would hide the
gaps in each.

### Wrong data (measurement error)

| Metric | Definition |
|---|---|
| Position age | Fetch time minus vehicle timestamp: median and 95th percentile |
| Impossible jumps | Consecutive positions from one vehicle implying more than 130 km/h (about 80 mph) |
| Off-route positions | Positions more than 100 meters (about 330 feet) from the trip's shape, divided by the positions tracked along that shape |
| Jitter | Displacement while the bus is stationary at a terminal: 95th percentile, meters |
| Clock anomalies | Vehicle timestamps in the future or going backwards |
| Prediction error | Predicted minus inferred actual arrival at 1, 2, 5, 10, 15 and 30 minutes ahead: bias, typical miss (median absolute error), mean absolute error, share within 60 s, and the number off by more than 30 minutes |

We measure position age at the first fetch that returned a given position.
Our tables can hold the same position twice, because a failed fetch in
between splits it into two rows, so we count each position once, by vehicle
and vehicle timestamp, at the first fetch that returned it. Positions that
stray off the route are counted only while a bus is being tracked along its
trip's shape, using the trip tracks (`trip-tracks`). Positions reported
before tracking began or after the bus reached the end of the line were
never compared with the shape, so they are in neither the count nor the
total.

To measure prediction error, we take each stop time we observed and compare
it with the prediction the feed was publishing a given number of minutes
earlier. A prediction counts as still published until 30 seconds after the
last fetch that returned it. The feed sends no `start_date`, which can leave
a prediction on the wrong day. For example, on November 19, 2026, about 1 to
2% of the predictions made 1 to 5 minutes ahead carried a clock time on the
next day. We count those as "dated another day" and leave them out of the
error figures.

Some predictions that do fall on the right day are still off by hours. On
November 20, 2026, about 2% of the predictions made 5 minutes ahead were off
by more than 30 minutes, and a few by 15 hours. They stay in the figures and
we count them. A handful of values that large pulls the mean far away from
what a rider would usually see: that day the mean miss was 9.2 minutes and
the typical miss, the median, was 2.4 minutes. So we report both, and the
site shows the typical miss.

Jitter tells us how much a parked bus's GPS position wanders. We look for a
dwell, which is 3 or more reports in a row from one vehicle, each moving
slower than 0.5 m/s (about 1 mph), within 150 meters (about 490 feet) of a
trip's first or last stop, and no more than 120 seconds apart. Jitter is the
distance of each report from the middle (median) position of its dwell.
Speed is whatever the vehicle itself reports.

### Data that doesn't match up (processing error)

| Metric | Definition |
|---|---|
| ID match rate | Positions whose `trip_id`, `route_id` and `stop_id` exist in the static GTFS in effect |
| Unassignable trips | Observed trips that cannot be tied to a scheduled trip on that day |
| Static feed integrity | Each day: is the zip valid, and are the required files present |
| Schedule agreement | Passio stop times at Trillium timepoints whose time matches a Trillium timepoint time at the same stop and route (exactly, within 1 minute, within 5 minutes). The two published schedules are not the same; Trillium's is out of date |
| Timepoint resolution | Printed timetable stop names tied to a Passio stop, divided by printed names. Falls if a service change renames or moves stops before the reference is updated |
| Parser rejects | Fetches whose body could not be decoded as a feed message, divided by fetches attempted |

### Collector health (paradata)

| Metric | Definition |
|---|---|
| Collector uptime | 20-second slots that hold a successful fetch, divided by slots in the day. The other slots are split into those with no logged fetch and those whose fetch failed |
| Latency | Response time: median and 95th percentile |
| Trip-update refresh | Of all pairs of consecutive 20-second fetches that both listed a trip, the share in which that trip's update had changed; and the typical time between the feed's own timestamps on two successive updates of a trip |
| Instrument changes | Dated log of static GTFS versions, collector versions and configuration changes |

The scheduler does not fire exactly on the 20-second mark, and its timing
drifts by a fraction of a second. If slots were cut at fixed clock times, a
tick a little early and the next a little late could land in the same slot
and leave the one between them looking empty. So the slots are centered on
the moment the scheduler usually fires that day, and a fetch belongs to the
day its slot falls in.

Every median and percentile in these files is computed the same way, by
straight-line interpolation between the two nearest values, so the median of
an even number of values is the average of the middle two.

(Code: `pipeline/quality/metrics.py`; medians and shares in
`pipeline/stats.py`; holidays in `pipeline/gtfs/holidays.py`. The measures built on our inferred stop
times, which are stop-level coverage, off-route positions and prediction
error, are in `pipeline/quality/stops.py` and appear under `events` in each
day's file. Randomness of gaps is in `pipeline/quality/gaps.py`.)

## 3. Arrival and departure times

The feed tells us where a bus is, but never when it reached a stop, so our
script figures out stop times in five steps.

First, the script places each stop on the trip's shape, the line the route
follows on a map. A shape can pass the same stop more than once, on loops or
out-and-back roads, so every pass within 150 meters (about 490 feet) counts
as a candidate, and the script picks the placements that keep the stops in
order while keeping them as close to the shape as possible overall. Before
doing that, it fixes any stretch where the map line doubles back on itself,
drawing the same piece of road forward, backward and forward again, by
keeping just one copy. Passio's route 1C shapes repeat every segment this
way, which made them about 21 km (13 miles) too long, and we report the
meters removed as a processing error. If a stop cannot be placed in order
within 150 meters (about 490 feet) of the shape, the script marks it
`unplaced` and gives it no time, and each placed stop's distance from the
shape is written beside it.

Second, the script projects each bus position onto the shape to find how far
along the route the bus has traveled. Tracking starts at the first position
within 300 meters (about 1,000 feet) of the start of the shape, because a bus
often shows its next trip's id while it is still finishing the last one. If
there is no such position, tracking starts 2 minutes before the scheduled
departure. It stops when the bus is within 50 meters (about 160 feet) of the
end of the shape, but never before the bus has reached the point where its
arrival at the last stop is timed.

Third, the script insists that the bus keep moving forward, so it searches
only a window ahead of the last distance it matched. That is how it copes
with loops and with stops the route visits twice. Sometimes two positions in
a row land on a later part of the shape, within 30 meters (about 100 feet) of
it and heading within 45 degrees of the shape's direction there. When that
happens, the script concludes that the bus left out part of the published
route, such as a loop it only drives on request, and moves tracking forward.
Stops in the stretch the bus left out get no time, and the script marks them
`skipped`.

Fourth, for each stop the script estimates the moment the bus crossed it by
interpolating, that is, by drawing a straight line in time between the
positions just before and just after the stop.

Fifth, it turns those crossings into an arrival and a departure. The arrival
is the first moment the bus is within 40 meters (about 130 feet) of the stop
along the shape, and the departure is the last, the moment the bus gets more
than 40 meters past it. The script never lets distance along the shape go
backwards, so GPS wobble cannot make a bus seem to cross a stop twice. It
also sets aside, and counts, any position more than 100 meters (about 330
feet) from the shape or moving against the route's direction faster than
3 m/s (about 7 mph).

For the on-time figures we use only the timepoints printed in Island
Transit's timetables. Each inferred stop time remembers how far apart in
time its two positions were. If they were more than 120 seconds apart, we
don't trust a straight line across the gap, and we record the stop as not
observed.

The table below lists the problems we know about and what we do about each
one.

| Problem | Handling |
|---|---|
| Layovers at terminals | First stop: departure only. Last stop: arrival only |
| Vehicles with no trip | Excluded; counted as over-coverage |
| Trip label changes early or late at a layover | Positions accepted only within the trip's scheduled window ± 30 minutes and on its shape |
| Detours | Positions more than 100 meters (about 330 feet) off the shape are set aside and counted. A stop the bus passed while off the shape gets no time and counts as not observed |
| Parts of the shape not driven | Tracking moves past them once two positions agree; their stops are `skipped`, counted as not observed |
| Gaps in positions | No interpolation across more than 120 s |
| Interpolated schedule times | On-time figures use true timepoints only: the stops printed in Island Transit's public timetables for the service period, matched to Passio stops on the same route. Where a route prints separate timetables for weekdays and Saturdays, each day uses the stops of its own timetable. Printed names that cannot be tied to one stop are left out and counted. Scheduled times are Passio's, the schedule the real-time feed follows (`pipeline/gtfs/timepoints.py`, `reference/`, `timepoints/`) |
| Schedule changes daily | Each service day is pinned to the static version in effect that day, and keeps it. If two complete downloads on one day differ, the day gets the version whose scheduled trips the live feed reported |
| A trip the schedule files describe badly | If a trip names a stop that is missing from the schedule, that trip gets no stop times and is counted (`problem` in `trip-tracks`); the rest of the day is unaffected |

(Code: `pipeline/events/infer.py`; timepoints: `pipeline/gtfs/timepoints.py`.)

## 4. On-time performance

A bus's schedule deviation is how far its actual departure from a timepoint
was from the scheduled departure. At the last stop of a trip we use the
arrival instead.

A departure counts as on time if it falls anywhere from 1 minute early to
5 minutes late, with both ends included. An early arrival at the last stop
also counts as on time, because a bus that reaches the end of its line early
has not made anyone miss it. Anything outside that window is early or late.

When we could not figure out a time for a scheduled timepoint, we call it not
observed. We report those separately and never count them as late. The feed
cannot tell a canceled trip apart from one that ran without reporting,
unless an alert or a `CANCELED` flag says so. We look for both every day and
publish the count of canceled trips with the data quality figures. Through
November 20, 2026 the feed had sent no such flag and no alert at all, so a
bus that did not run still looks the same as a bus that did not report.
That is why we always show the on-time figures next to the share of
scheduled departures we actually observed.

The typical deviation we report is the median. Figures for a route over the
last 28 days pool every departure in those days, and leave out any day on
which Island Transit runs no service. When Island Transit brings in a new
timetable, the pooled figures start over on the day it takes effect, so
that stops and times from the old timetable are not mixed with the new one.

(Code: `pipeline/events/ontime.py`; the 28-day route tables are built by
`pipeline/events/routes.py`.)

## 5. Validation

Three checks back up the inferred times and the on-time figures.

Check 1 is the field check. We time arrivals and departures in person, using
a clock synced to the network, and compare those times with ours. We report
the bias (whether our times tend to run early or late), the mean absolute
error (the average size of the difference), and the share within 30 and 60
seconds. The target is a mean absolute error of 30 seconds or less, and we
judge it only once at least five moments have been timed. It is a
spot check of whether the tracking matches what happens on the street, and
so far we have only timed route 60, the route we ride. That means it does
not yet cover other routes, departures after a bus has waited at a
timepoint, or departures from a terminal. Checks 2 and 3 cover every route
every day.

Check 2 asks whether our stop times agree with the stop sequence the feed
itself reports (`current_stop_sequence`). Check 3 asks whether any travel
time between stops runs backwards or implies an impossible speed without a
good explanation.

Every night our script scores check 1 against the timed observations. We
time the same moments our method looks for: the bus coming within 40 meters
(about 130 feet) of the stop, and the bus getting more than 40 meters past
it. Riding on board, it is often easier to note the moment the bus is level
with the stop, and we compare that with the midpoint of our inferred arrival
and departure. The script matches each timed moment to our stop time for
the same service date, trip and stop, and each difference is our time minus
the timed one. A loop trip visits its terminal twice, so there the script
uses whichever visit is nearer in time to the timed moment. When we have no inferred time for a timed moment, we count it
but don't score it.

Checks 2 and 3 also run every night, and their results go into each day's
on-time file.

For check 2, it helps to know that `current_stop_sequence` names the stop a
bus is heading for. When the bus passes stop k, that number changes to k + 1
somewhere between the last report showing k or less and the first report
showing more. Our departure time agrees with the feed if it falls inside
that interval, widened by 30 seconds on each side. We leave out first and
last stops. For example, on November 19, 2026, 95% of 2,902 inferred
departures agreed, and the first report naming the next stop came a median
of 3.8 seconds after our departure time.

For check 3, our times along a trip, from the departure at one stop to the
arrival at the next, must never run backwards or imply a speed above
35 m/s (about 78 mph). We don't measure segments across a stretch the bus
left out. On November 19, 2026, none ran backwards, and 29 of 5,919 went
faster than 35 m/s. Every one of those fell within a single 16 to 40 second
gap between reports on a fast road, where the projection can jump ahead as
far as the 35 m/s search limit allows. The day file lists the fastest ones.

(Code and files: check 1 is scored by `pipeline/events/field.py` from
`reference/field-observations.csv` into `ontime/field-check.json`, and the
observer's guide is `docs/field-check.md`. Checks 2 and 3 run in
`pipeline/events/validate.py` and are written to `validation` in
`ontime/days/<date>.json`.)
