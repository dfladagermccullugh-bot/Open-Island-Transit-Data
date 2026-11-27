# Corrections

Every change that moved a published figure, newest first. Each row says what
was wrong, what the figures were before and after, and the version of the code
that made the change. The full reasoning is in `docs/decisions.md`, and every
day is rebuilt with the new code, so the files on the `data` branch always
hold the corrected figures. The site's page "What this data can and can't tell
you" shows this list.

| Date | What was wrong | Before | After | Version |
|---|---|---|---|---|
| November 27, 2026 | A trip was followed only while the bus showed its id, and buses often switch ids at the wrong moment, so many first and last stops had no time | 79.5% of timepoint events observed (November 19 to 25) | 85.9% | `events/0.5.0` |
| November 27, 2026 | GPS glitches, positions a bus could not have reached, could time a stop | 22 glitch positions used on November 24 | removed; 2 stop times changed | `events/0.4.0` |
| November 27, 2026 | Printed timepoints were matched to stops without regard to direction, so eight pointed at the stop across the road, where those buses never stop | route 60 on time 53.8%; all routes 73.4% (November 19 to 25) | 59.8%; 73.8% | `timepoints/0.2.0` |
