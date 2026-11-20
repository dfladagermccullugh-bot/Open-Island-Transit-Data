# Reference data

Material copied from Island Transit's public website. The pipeline reads it
to identify true timepoints (`pipeline/gtfs/timepoints.py`); it is not
produced by the collector.

| File | Contents |
|---|---|
| `timetable-timepoints-<date>.csv` | The stops printed in each route timetable of the service guides effective `<date>`, in printed order, without times. 2026-09-27: Whidbey, Camano and NAS Whidbey guide PDFs from islandtransit.org/download-digital-schedules, read on 2026-11-19 with an automated PDF reader and checked by hand |
| `timetable-overrides-<date>.csv` | Reviewed links from printed names to Passio stop ids where name matching fails, and names deliberately left unresolved, with the reason |
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
