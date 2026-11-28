## Corrections

Every change that moved a published figure, newest first. Each row says what
was wrong, what the figures were before and after, and the version of the code
that made the change. The full reasoning is in `docs/decisions.md`, and every
day is rebuilt with the new code, so the files on the `data` branch always
hold the corrected figures. The site's page "What this data can and can't tell
you" shows this list.

| Date | What was wrong | Before | After | Version |
|---|---|---|---|---|
| November 28, 2026 | The Compare page recounted our figure under our own rule from deviations rounded to the second, which put departures a fraction of a second outside a limit inside it. It also set one evening sample of each agency, taken the day after Thanksgiving, beside our 28-day figure | recount under our rule 70.1% (November 19 to 25); Intercity 83.6%, Skagit 69.1%, BC Transit (Victoria) 72.3%, Halifax 70.0% on time from one sample each | 70.0% (74.2% on the home page less 556 early arrivals at last stops, of 13,095); two agencies, Skagit and Intercity, with no pooled figure until the samples of December 1, 2026 | `compare/0.3.0` |
| November 28, 2026 | The headline's range covered chance only. It did not say how many departures we could not time, why, or how far they could move the figure. No figure changed; these were added | on time 74.22%, very likely 72.6% to 75.8% by chance (November 19 to 25) | the same, plus 1,569 of 14,664 departures untimed (the feed went quiet 506, part of the route left out 393, the trip sent nothing 385, the bus stopped reporting first 224, already past 61); worst case 66.3% to 77.0% of scheduled departures; weighted for the missed ones 73.7% (72.0% to 75.2%) | `periods/0.3.0` |
| November 28, 2026 | Buses wait in bays up to 160 meters (about 520 feet) from where the schedule puts a trip's first or last stop, so many of those stops had no time | timepoint events observed 85.9%, on time 73.85% (November 19 to 25); last stops timed 73.3%, first stops 77.6% (November 20 to 25) | 89.3%, 74.22%; 90.0%, 88.0% | `events/0.6.0` |
| November 27, 2026 | A trip was followed only while the bus showed its id, and buses often switch ids at the wrong moment, so many first and last stops had no time | 79.5% of timepoint events observed (November 19 to 25) | 85.9% | `events/0.5.0` |
| November 27, 2026 | GPS glitches, positions a bus could not have reached, could time a stop | 22 glitch positions used on November 24 | removed; 2 stop times changed | `events/0.4.0` |
| November 27, 2026 | Printed timepoints were matched to stops without regard to direction, so eight pointed at the stop across the road, where those buses never stop | route 60 on time 53.8%; all routes 73.4% (November 19 to 25) | 59.8%; 73.8% | `timepoints/0.2.0` |
