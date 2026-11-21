# Open Island Transit Data

How on time are Island Transit's buses? Every few seconds, each bus on
Whidbey and Camano Islands in Washington reports where it is, and each
report vanishes as soon as the next one arrives. This project saves every
one of them, works out how often each bus left its stops on time, and checks
how trustworthy the tracking data is. Everything is free to download.

You can see the results at
<https://dfladagermccullugh-bot.github.io/Open-Island-Transit-Data/>.

We have been collecting since November 19, 2026, every 20 seconds, around
the clock.

## What you can find here

The site has four main pages. Routes shows a map of every route, colored by
how often its buses left on time, and lets you open any route to see the
stops where its buses fall behind. On time follows the whole system day by
day and hour by hour. Data quality asks whether the tracking data can be
trusted, showing how often it misses buses, drops out, goes stale, puts
buses off their route, or predicts arrivals wrongly. Download holds every
saved update and every table we build from them, one file per day, plus a
monthly report with checksums you can use to check your copies.

A departure counts as on time when the bus leaves no more than 1 minute
early or 5 minutes late. We measure it at the stops printed in Island
Transit's timetables, because those are the times riders are promised. When
the tracking can't see a departure, we count it separately and never count
it as late.

## How it works

```mermaid
flowchart LR
  B[The bus<br/>GPS reports its location] --> F[Island Transit's live feed<br/>each update replaces the last]
  F -->|every 20 s| C[Collector<br/>saves every update]
  C -->|each night| N[Nightly checks<br/>stop times, on-time figures,<br/>data quality grades]
  N --> S[Site and free downloads]
```

A small program, the collector, asks the feed for bus locations, arrival
predictions and alerts every 20 seconds. It saves exactly what comes back
and keeps a log of every request, and it runs on a free hosted database
(`supabase/`). Each night, the previous day's saved data goes to
[GitHub Releases](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/releases),
one file per day, with checksums.

The feed tells us where a bus is, but it never says when the bus left a
stop. So each night our script traces every bus along its route and works
out when it passed each stop (`pipeline/events/`). The script checks that
work every day against the stop changes the feed itself reports, and we
also time buses in person on route 60 to compare.

Our scripts then grade the feed the way survey researchers grade any source
of data (`pipeline/quality/`). Coverage asks whether every scheduled trip
appears at all. Nonresponse looks for gaps and failed requests. Measurement
error covers GPS wobble, stale locations and wrong predictions. Processing
error catches IDs that don't match the schedule, and paradata is the log of
the collection itself. After each nightly run, the site rebuilds itself with
the new numbers (`site/`).

## Data

| Where | What |
|---|---|
| Releases `raw-YYYY-MM` | Every response from the feed, exactly as received; the request log; the schedule files |
| Releases `parsed-YYYY-MM` | Tables of bus locations, arrival predictions, alerts, and inferred stop arrivals and departures (Parquet) |
| [`data` branch](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/tree/data) | Daily quality and on-time figures (JSON and CSV), route maps, monthly reports, and manifests listing every file with its size and SHA-256 |

The [codebook](docs/codebook.md) describes every file and column, the
[methods](docs/methods.md) give the exact definition behind every number,
and the [decisions](docs/decisions.md) log records every design choice and
the reason for it.

To see whether the collector is running right now, ask it for its status:

```bash
curl -s "https://ekczqlmunyumtgtthppb.supabase.co/rest/v1/status_now" \
  -H "apikey: sb_publishable_AWlvBYVrlTz7dEaf7RwwzA_5zRN3af-"
```

## Repository layout

| Path | Contents |
|---|---|
| `supabase/` | The collector: database tables, scheduler, public read-only views, and the collecting function |
| `pipeline/` | Archive, parsing, stop-time inference, quality metrics, monthly reports (Python) |
| `site/` | The public site (Quarto with Observable Plot and Leaflet) |
| `reference/` | Timepoints from Island Transit's printed timetables; field observations |
| `scripts/` | Health check, feed check, a local rehearsal of the nightly run |
| `tests/` | Tests and fixtures |
| `docs/` | Methods, codebook, decisions, collector setup, field-check guide |

## Run the checks yourself

```bash
pip install -e ".[dev]"
pytest
python scripts/verify_feed.py
```

```bash
deno test --allow-read supabase/functions/collect/
```

Run `verify_feed.py` during service hours, roughly 6 AM to 7 PM Pacific, so
that there are buses on the road for it to check.

## Licenses

The code is released under the MIT license ([`LICENSE`](LICENSE)). The data
terms are in [`LICENSE-DATA.md`](LICENSE-DATA.md): Island Transit published
no license for the source feeds, and the tables we derive from them are
CC BY 4.0. The map background on the site is © OpenStreetMap contributors.
The feeds describe vehicles only and contain no information about
individual riders.

This project is independent and has no connection to Island Transit or
Passio Technologies. Every number on the site comes from code in this
repository, run on Island Transit's public data.
