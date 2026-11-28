# A five-minute tour

This tour is for anyone who wants to see what Open Island Transit Data does
and how it holds up, without reading the whole repository first. Each stop
takes under a minute. Every link opens the live site at
<https://islandtransitdata.org/> or a file in this repository, and none of
them needs an account.

## 1. The question

Open the [home page](https://islandtransitdata.org/). Its title asks the
one question the project exists to answer: how on time are Island
Transit's buses? Island Transit runs the free buses on Whidbey and Camano
Islands in Washington. Its buses report where they are every few seconds,
and each report is gone as soon as the next one arrives. Our collector
checks the feed every 20 seconds, day and night, and saves what it says, so
the record stays.

## 2. The headline and how sure it is

The big number on the home page is the share of departures that left on
time, from 1 minute early to 5 minutes late, over the last 28 days with
service. Below it, the counts say how many departures were scheduled and
how many of those we could time at all. Open "How sure are we?" under the
number. It gives three answers, because a figure can be off for three
different reasons: chance in which days we happened to see, the departures
the feed never let us time, and the worst case if every one of those had
been late or every one on time. The
[Limits page](https://islandtransitdata.org/limits.html#how-sure) explains
the same three answers at more length.

## 3. One route

Open [route 60](https://islandtransitdata.org/routes.html#60), which runs
from the Clinton ferry terminal to Langley and Freeland. The map colors it
by how often its buses left on time, and the chart below shows every
timepoint on the route, with how early or late buses usually were there.
A timepoint is a stop printed in Island Transit's timetable, and those are
the only stops we judge, because their times are the ones riders are
promised. Route 60 is also the route we ride to time buses in person, which
is the field check in [Methods, section 5](docs/methods.md#5-validation).

## 4. One stop

Open the [Clinton ferry terminal](https://islandtransitdata.org/stops.html#178409).
It lists the stop's recent departures against the timetable, and you can
open any trip there to see how it ran stop by stop. Because this stop is a
ferry terminal, a note at the top says how many buses we timed there over
the last 28 days with service and how many of them were on time. You can
also type any route or stop into the box at the top of the home page to
get to its page.

## 5. One replayed day

Open [Replay a day](https://islandtransitdata.org/replay.html). It plays
back where every bus was on one of the last 14 days, from the positions we
saved, at whatever speed you choose. It is the plainest way to see what the
raw feed looks like, gaps and all: watch for a bus that goes quiet for a
few minutes, or one that sits at a terminal showing its next trip's number.

## 6. The gate report

Look at the bottom of any page. The footer says how many automatic checks
every figure on the site passed, and on what day. Those checks are the
publishing gate. Each night our programs write their new figures to a
holding branch, and the public `data` branch, which the site reads, moves
forward only when every rule passes. The report itself is
[`checks/gate.json`](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/blob/data/checks/gate.json)
on the `data` branch: the rules it ran, any problem it found, and a link to
the run that checked them. The rules are described in
[Methods, section 5](docs/methods.md#5-validation) and written in
[`pipeline/invariants.py`](pipeline/invariants.py).

## 7. Rebuild a day yourself

You don't have to trust our copy of the figures. With Python and an
internet connection, two commands rebuild any published day from its raw
files and compare every file with the one we published:

```bash
pip install -e .
python scripts/reproduce.py --date 2026-11-20
```

The script downloads that day's raw feed files from our Releases, checks
each against its published SHA-256 fingerprint, runs the same code the
nightly job runs, and prints whether every file came out identical, with
the day's main figures side by side. It takes a few minutes. A fresh
computer at GitHub does the same for three random days every week, and the
[Limits page](https://islandtransitdata.org/limits.html#rebuild) shows the
latest result.

## 8. Where the survey-error framework shows up

The project treats the live feed the way a survey researcher treats a
survey, as a source of data that can go wrong in known ways. The
[Data quality page](https://islandtransitdata.org/quality.html) and
[Methods, section 2](docs/methods.md#2-feed-quality-metrics) sort the
measures into those kinds. Coverage asks whether every scheduled trip shows
up in the feed at all. Nonresponse covers failed requests, empty feeds and
gaps in a bus's reports. Measurement error covers GPS jumps, stale
positions and wrong arrival predictions. Processing error covers ids that
don't match the schedule. Paradata is the log of every request the
collector made. The same thinking carries into the headline: a departure
we could not time is treated like a person who didn't answer a survey, so
it is never counted as late, and the weighted share in "How sure are we?"
adjusts for those missed departures the way surveys adjust for people who
didn't answer
([Methods, "How sure we are"](docs/methods.md#how-sure-we-are)).

## If you have more time

The [decisions log](docs/decisions.md) records every choice we made and
why, with the figures before and after each change. The
[corrections list](docs/corrections.md) records every published figure that
moved. The [codebook](docs/codebook.md) describes every file and column.
