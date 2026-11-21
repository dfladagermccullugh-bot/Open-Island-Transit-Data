# Reference data

Material copied from Island Transit's public website. The pipeline reads it
to identify true timepoints (`pipeline/gtfs/timepoints.py`); it is not
produced by the collector.

| File | Contents |
|---|---|
| `timetable-timepoints-<date>.csv` | The stops printed in each route timetable of the service guides effective `<date>`, in printed order, without times. 2026-09-27: Whidbey, Camano and NAS Whidbey guide PDFs from islandtransit.org/download-digital-schedules, read on 2026-11-19 with an automated PDF reader and checked by hand. 2026-12-27: the Whidbey guide posted on islandtransit.org/december-service-changes (`IT-WhidbeyServiceGuide-Dec26rev.pdf`), read on 2026-11-21 the same way and compared block by block with the September rows; the Camano and NAS Whidbey guides on the download page were still the September ones that day, so their rows are carried over. As in September, a route's weekend timetable is listed only where it prints different stops from the weekday one (route 4) |
| `timetable-overrides-<date>.csv` | Reviewed links from printed names to Passio stop ids where name matching fails, and names deliberately left unresolved, with the reason |
| `no-service-days.csv` | The holidays on which Island Transit runs no service, each with the page it was read from and the date. Passio's schedule has no holiday exceptions, so these days are flagged `service_expected: false` and left out of figures that pool several days (`pipeline/gtfs/holidays.py`). The file lists the holidays themselves; add the day of observance when a holiday falls on a weekend and Island Transit announces one |
| `static-pin-overrides.csv` | Corrections to the schedule version a service day is measured against (`pipeline/gtfs/versions.py`), one reviewed row per day and source: `service_date`, `source` (`passio` or `trillium`), `version` (an id from `manifest/static.json`), `reason`, `reviewed_on`. A row replaces the day's pin on the next nightly run, the replaced version is recorded in the manifest, and the day's stop events and quality figures are rebuilt. Empty unless a day was pinned wrongly; to undo a row, add the version back with a new row |
| `field-observations.csv` | Arrivals and departures timed in person at timepoints for the field check (`docs/field-check.md`), one row per bus and stop, in the sheet format of `python -m pipeline.events.field sheet`. Scored nightly by `pipeline/events/field.py` |
| `timetable-timepoints-matched.csv` | Output of `scripts/check_timepoints.py`: each printed stop, its Passio stop, and whether Trillium marks it as a timepoint. For checking only |

The pipeline uses the latest file whose date is on or before the service day.

## When the service changes

1. Download the new service guides and list each timetable's printed stops in
   a new `timetable-timepoints-<effective date>.csv` (same columns).
2. Copy the previous overrides file to `timetable-overrides-<effective
   date>.csv` and run `scripts/check_timepoints.py <passio zip> <trillium zip>`
   on a Passio zip downloaded after the change.
3. Review the unresolved names and low match scores; add or remove overrides.
4. Commit. The next nightly run builds a new `timepoints/` folder for days on
   and after the effective date.

The files for a coming change can be committed early: they are used only
from their date. Before that date, `timepoints.build` can be run on the
current Passio zip to see which printed names resolve. After the change,
repeat step 2 on a Passio zip downloaded on or after the effective date.
