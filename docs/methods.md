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
| Randomness of gaps | Checks whether missed trips happen at random or cluster around certain routes, times of day or busy hours. Rates: the share of scheduled trips with no position report within 30 minutes of their schedule, by hour, route, day type and number of trips in service, over all days. Model: a logistic regression of "trip not observed" on hour band (before 7, 7 to 9, 9 to 15 as the reference, 15 to 18, and 18 on), route (routes with under 30 trips pooled), day type and the number of scheduled trips in service at the trip's start. Firth's penalized likelihood keeps estimates finite when a route has no unobserved trips. The likelihood-ratio test of all predictors together tests whether gaps are missing completely at random. Fitted from 14 days and 30 unobserved trips |

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
| GPS glitches | Positions left out before tracking because the bus would have had to jump somewhere and back faster than 130 km/h (section 3), and impossible jumps that were kept because there was no jump back (unresolved) |
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
site shows the typical miss. Since November 27, 2026, we also keep 20
points that split each day's predictions into twenty equal groups (the
2.5th, 7.5th and so on up to the 97.5th percentile of the miss). The site
draws them as 20 dots, so a reader can see what happened to 20 buses the
feed said were 5 minutes away.

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

### What the official validators say

Besides our own measures, we run the two checkers that MobilityData, the
nonprofit that looks after the GTFS standards, publishes for anyone to use.
Its schedule validator reads each new version of Passio's schedule files
once, the first time we use that version, and lists every rule of the
standard the files break, from errors (a rule that must hold) to warnings
and notes. Its realtime validator reads one hour of the live feed each week:
every vehicle position and trip update we saved from 10 to 11 AM Pacific on
the latest weekday, in order, against the schedule we used that day. We keep
only what each found: the rule, how serious it is and how often it fired.
On the schedule of November 19, 2026, the schedule validator found 22
errors, all of one kind: a stop time whose arrival comes before the
departure from the stop before it. Our automatic data check (tier 2) raises
an error the validators find in a new schedule or a new week that they did
not find in the one before.

(Code: `pipeline/quality/metrics.py`; medians and shares in
`pipeline/stats.py`; holidays in `pipeline/gtfs/holidays.py`. The measures built on our inferred stop
times, which are stop-level coverage, off-route positions and prediction
error, are in `pipeline/quality/stops.py` and appear under `events` in each
day's file. Randomness of gaps is in `pipeline/quality/gaps.py`. The official validators
run in `pipeline/feed_validation.py`, started by
`.github/workflows/validators.yml` with the schedule validator 8.0.1 and the
realtime validator built from a pinned commit; the check that reads them is
in `scripts/anomaly.py`.)

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

Before tracking a bus, the script sets aside GPS glitches. It lines up each
bus's positions in time order, whatever trip each one names, and works out
how fast the bus would have had to go between them. A bus that seems to
travel faster than 130 km/h (about 80 mph) to a spot and then straight back,
to where its positions before and after agree, did not really go there. The
script leaves out that position, or that run of positions if it lasts two
minutes or less. When a bus jumps and never comes back, we can't tell which
side of the jump is wrong, so the script keeps both and counts the jump as
unresolved. Most unresolved jumps are a position whose time stamp is a few
seconds off, which barely moves a stop time. On November 24, 2026, between
about 5 and 10 in the morning, the feed sent many glitches: our script left
out 22 positions from 12 buses and counted 42 unresolved jumps. On the six
other days to November 25 it left out none.

Second, the script projects each bus position onto the shape to find how far
along the route the bus has traveled. Tracking starts at the first position
within 300 meters (about 1,000 feet) of the start of the shape, because a bus
often shows its next trip's id while it is still finishing the last one. If
there is no such position, tracking starts 2 minutes before the scheduled
departure. It stops when the bus is within 50 meters (about 160 feet) of the
end of the shape, but never before the bus has reached the point where its
arrival at the last stop is timed.

The feed names the trip each bus is on, but the name often changes at the
wrong moment. A bus may show its next trip's id before it reaches the last
stop of the trip it is on, or keep the last trip's id while it waits at the
first stop of the next one, or send no trip id for a while after a driver
logs off. Buses also start the day at the depot between Greenbank and
Coupeville and drive to their first stop, change routes during the day, and
sometimes get a new driver partway along a route. So since November 27,
2026, our script follows each bus, not just each trip id. Each trip also
uses the positions its bus sent under any other trip id, or none, from 30
minutes before the trip's start (or from the end of the bus's previous
trip, if that is later) to 30 minutes after its scheduled end, but never
past the moment the bus left the first stop of its next trip. Tracking still
starts only near the start of the route and stops at its end, so the drive
from the depot and the next trip are not mistaken for this one. When a bus
arrives along the road it will leave by, tracking starts at its position
nearest the first stop, on the visit to the start of the route that ends
closest to the scheduled departure. Each stop time records whether it came
from the trip's own positions or borrowed ones. On November 25, 2026, this
took the last stops our script could not time from 170 of 339 to 43, and
the first stops it saw only after the bus had left from 92 of 341 to 24.

Sometimes two pieces of the shape are exactly as close to a position as each
other. This happens when the nearest point is a corner where two pieces meet,
and when the shape draws the same stretch of road out and back. Computers
round the last digits of such a distance in slightly different ways, so the
choice between the two pieces used to depend on which machine ran the script.
Now the script treats distances within one micrometer (a millionth of a
meter, far finer than GPS can tell apart) as equal and takes the piece that
comes first along the route. A position nearest to a corner gets the corner's
own distance along the route, and the script checks the bus's direction
against the piece that leaves the corner. The same rule applies when the
script places stops on the shape, so the same inputs give the same times on
any computer.

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
Transit's timetables. Each printed timetable reads in one direction, such as
northbound, and our script first works out which of Passio's two directions
runs in that order. It then looks for each printed stop name only among the
stops that buses going that way actually serve. This matters because many
stops come in pairs, one on each side of the road, often with the same name.
Until November 27, 2026, our script could pick the stop across the road, and
then the buses that really stop there were never scored. On route 60, four
northbound timepoints had this problem, among them Bayview P&R. Each inferred stop time remembers how far apart in
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
| Interpolated schedule times | On-time figures use true timepoints only: the stops printed in Island Transit's public timetables for the service period, matched to Passio stops on the same route and in the same direction. Where a route prints separate timetables for weekdays and Saturdays, each day uses the stops of its own timetable. Printed names that cannot be tied to one stop are left out and counted. Scheduled times are Passio's, the schedule the real-time feed follows (`pipeline/gtfs/timepoints.py`, `reference/`, `timepoints/`) |
| Schedule changes daily | Each service day is pinned to the static version in effect that day, and keeps it. If two complete downloads on one day differ, the day gets the version whose scheduled trips the live feed reported |
| A trip the schedule files describe badly | If a trip names a stop that is missing from the schedule, that trip gets no stop times and is counted (`problem` in `trip-tracks`); the rest of the day is unaffected |

(Code: `pipeline/events/infer.py`; following the bus across trip ids:
`pipeline/events/continuity.py`; GPS glitches: `pipeline/glitch.py`;
shapes, projection and the rule for equal distances:
`pipeline/events/geometry.py`; timepoints: `pipeline/gtfs/timepoints.py`.)

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
publish the count of canceled trips with the data quality figures. Since
November 27, 2026, a trip the feed says was canceled is left out of the
on-time counts altogether, because a bus that did not run cannot be on time
or late, and the number of its timepoints left out is published. Through
November 20, 2026 the feed had sent no such flag and no alert at all, so a
bus that did not run still looks the same as a bus that did not report.
That is why we always show the on-time figures next to the share of
scheduled departures we actually observed.

The typical deviation we report is the median. Figures for a route over the
last 28 days pool every departure in those days, and leave out any day on
which Island Transit runs no service. When Island Transit brings in a new
timetable, the pooled figures start over on the day it takes effect, so
that stops and times from the old timetable are not mixed with the new one.

The headline share on the home page pools the last 28 days with service,
and since November 27, 2026 it comes with a range. Departures of one trip
are not independent of each other, because a bus that falls behind tends to
stay behind, so we draw whole trips. We make 2,000 new sets of trips, each
as large as the real set and drawn from it at random with repeats allowed,
compute the on-time share of each, and report the middle 95% of those
shares. This is called a bootstrap. The range says how much the share could
move by chance in which trips happened to run late; it does not cover the
departures we could not time.

(Code: `pipeline/events/ontime.py`; the 28-day route tables are built by
`pipeline/events/routes.py`; the headline and its range by
`pipeline/periods.py`, `trip_interval`.)

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

Each night our script also looks for a route whose buses run a steady few
minutes off the timetable all along their trips. For each trip with at
least four timed stops, it takes the middle value of how late or early the
bus was and how widely that varied. A bus held up by traffic gets later as
it goes, so its lateness varies. A bus that keeps the timetable's pace but
four or more minutes from its clock times, varying by less than a minute,
looks instead like a schedule that is wrong for that day. If half or more
of a route's trips look like that, the day file flags the route. From
November 19 to 25, 2026, no route was flagged.

Before the site is rebuilt each night, a script checks that the published
numbers agree with each other. It adds up every day's counts by route, by
hour and by stop and makes sure each set adds up to the day's total. It makes
sure every percentage is the count it came from divided by its total, that
the daily tables match the day files cell for cell, and that the 28-day
figures on the home and routes pages are the sums of the days they cover. It
looks for missing days, for days made by an older version of our code, and
for a bus timed on two trips at once. For the last three days, it counts the
on-time figures again from the stop times themselves. Our programs write
their new figures to a holding branch first, and the public `data` branch,
which the site and every reader use, moves forward to them only when every
rule passes. If any rule fails, the `data` branch and the site keep the last
figures that passed until the problem is fixed, and the watchdog names the
failed rule. The daily tables and stop times are saved under names that
include a short fingerprint of their contents, so a rebuilt file never
replaces one the `data` branch still points to. These rules check our arithmetic, not the buses: they catch a
number that was added up or copied wrong, but not a stop time that was
inferred wrong. Checks 1, 2 and 3 are there for that.

(Code and files: check 1 is scored by `pipeline/events/field.py` from
`reference/field-observations.csv` into `ontime/field-check.json`, and the
observer's guide is `docs/field-check.md`. Checks 2 and 3 run in
`pipeline/events/validate.py` and are written to `validation` in
`ontime/days/<date>.json`. The publishing rules are I1 to I12 in
`pipeline/invariants.py`; the nightly job runs them over `data-staging` and
`scripts/promote_data.sh` fast-forwards `data` to the checked commit only
when they pass, with their report in `checks/gate.json`. The comparison and
validator jobs run the first eight (`scripts/light_gate_promote.sh`,
`checks/gate-light.json`), and the site job runs them again before it copies
the data in. Parsed and stop-event files are named by `pipeline/release.py`
(`stop-events-<date>-<first 8 characters of the SHA-256>.parquet`, recorded
as `asset` in the manifests; raw files keep plain names), and `python -m pipeline.release prune` deletes
a file only once neither the new `data` commit nor the one before it names
it, never in a `raw-*` release.
`scripts/check_versions.py` makes sure a change to the code behind a number
comes with a new version, so every day is rebuilt with it.)

## 6. Comparisons

Bus agencies define "on time" in different ways, so their published
figures can't be set side by side as they are. We keep each agency's
published rule and figure, with its source, in a reference file, and put
each on the same footing as ours in one of two ways.

The first, which we mark R, recounts our own departures at timepoints over
the last 28 days with service under the other agency's window, for example
up to 3 minutes late instead of 5. In this recount every timed moment counts
alike, so an early arrival at a last stop counts as early, which our own
published figure does not. That makes the recount under our own rule a
little lower than our published figure, and it is the number the recounts
under other rules should be read against.

The second, which we mark B, turns the other agency's published figure into
a figure under our window, 1 minute early to 5 minutes late. We don't have
that agency's departure times, so we assume they spread out like ours, but
wider or narrower by a factor of 0.4, 0.5, 0.6, 0.8, 1, 1.25 or 1.6. For each factor
we shift the whole spread later or earlier until it gives the agency's own
figure under its own rule, then count how much of it falls inside our
window. We report the lowest and highest of those results. When no shift
can reproduce the agency's figure for a factor, that factor is left out,
and the page says how many were used. A figure that only the narrow
factors can reproduce means that agency's buses keep closer to their
schedule than ours do. A rule without a window, such as the
excess wait time some cities use for frequent buses, can't be converted
this way, and its figure is listed as published only.

Neither way adjusts for what each agency times (a departure or an
arrival), at which stops, how it treats a trip that never ran, or the
period its figure covers.

A third way does (marked A). Some agencies publish their buses' live
locations openly, as Island Transit does. Once a week, at a busy hour in
that part of the world, we read two hours of each such feed every 20
seconds and run it through the same method we use here: the same stop
radius of 40 meters (about 130 feet), the agency's own timepoints (or every
stop if its schedule marks none) and our on-time rule. We report the share
on time with a 95% range from resampling whole trips, and the same share
with a stop radius of 30 and 50 meters (about 100 and 160 feet), to show
how much the radius matters. We keep only that summary, not the agency's
data. Two hours on one day is a small slice of a week of service, so it is
a spot check. On November 27, 2026, four agencies had open feeds: Skagit
Transit, Intercity Transit, BC Transit in Victoria and Halifax Transit.

(Code and files: `pipeline/compare.py` writes `compare/summary.json`; the
rules and figures, with sources, are in `reference/peers/definitions.csv`;
the samples are taken by `pipeline/compare_sample.py`, run by
`.github/workflows/compare.yml` from `reference/peers/feeds.csv`, and kept
as `compare/<agency>/<date>.json`.)
