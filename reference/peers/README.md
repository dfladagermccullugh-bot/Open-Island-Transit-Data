# Peers

Files for the comparisons with other bus systems (`docs/methods.md`,
section 6; `pipeline/compare.py`).

`definitions.csv` holds each agency's published on-time rule and latest
figure, with sources and how firm they are (`confidence`: high when an
official source states both the rule and the figure). It was researched on
November 27, 2026; an empty cell means nothing was found. A window is given
in minutes early and minutes late. Agencies that measure headways or excess
wait time instead have no window and are listed as published only.

`feeds.csv` lists the open live feeds (GTFS-Realtime vehicle positions and
the matching GTFS schedule) that the weekly `Compare` workflow samples for
class A comparisons. `region` is one of the workflow's regions: North
America, Central and South America, Europe, Asia. Run the workflow with
`mode: discover` to list candidates from the Mobility Database catalog.

The first list (November 27, 2026) comes from the discovery run of that day.
Of the 16 agencies, four publish both feeds openly: Skagit Transit,
Intercity Transit, BC Transit (Victoria) and Halifax Transit, all in North
America. Kitsap Transit's live feed is listed but its schedule is marked
inactive; Missoula's Mountain Line publishes a schedule only (the live feed
the catalog lists under that name is Flagstaff's); the others have no open
live vehicle positions in the catalog, or need a key.
