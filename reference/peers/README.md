# Peers

Files for the comparisons with other bus systems (`docs/methods.md`,
section 6; `pipeline/compare.py`).

`definitions.csv` holds each agency's published on-time rule and latest
figure, with sources and how firm they are (`confidence`: high when an
official source states both the rule and the figure). It was researched on
November 27, 2026; an empty cell means nothing was found. A window is given
in minutes early and minutes late.

`feeds.csv` lists the open live feeds (GTFS-Realtime vehicle positions and
the matching GTFS schedule) that the `Compare` workflow samples for class A
comparisons, on Tuesdays and Thursdays around midday. Run the workflow with
`mode: discover` to list candidates from the Mobility Database catalog.

`no-service-days.csv` lists US federal holidays, read from the Office of
Personnel Management on November 28, 2026. A sample taken on one of them or
on the day before or after is left out of the pool (`skip_reason` in
`pipeline/compare.py`), and so is the same day of Island Transit's.

Since `compare/0.3.0` (November 28, 2026) both files list two agencies,
Skagit Transit and Intercity Transit, the two Washington agencies with open
feeds of both kinds. The first lists of November 27 also held Kitsap
Transit, Mountain Line (Missoula), Cache Valley, BC Transit (Victoria),
Halifax Transit and agencies in Central and South America, Europe and Asia;
they are in the history of this folder, and why they were cut is in
`docs/decisions.md`. The code still takes any number of agencies.
