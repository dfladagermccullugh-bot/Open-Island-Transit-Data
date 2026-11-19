# Reference data

Material copied from Island Transit's public website, used to check the
pipeline. Not produced by the collector.

| File | Contents |
|---|---|
| `timetable-timepoints-2026-09-27.csv` | The stops printed in each route timetable of the service guides effective 2026-09-27 (Whidbey, Camano, NAS Whidbey PDFs at islandtransit.org/download-digital-schedules), in printed order. Read from the PDFs on 2026-11-19 with an automated PDF reader and checked by hand; times are not included |
| `timetable-overrides.csv` | Reviewed links from printed stop names to Passio stop ids where name matching fails, and names left unresolved with the reason |
| `timetable-timepoints-matched.csv` | Output of `scripts/check_timepoints.py`: each printed stop, the Passio stop it was matched to, and whether Trillium marks it as a timepoint |

Rebuild the matched file with:

    python scripts/check_timepoints.py <passio zip> <trillium zip>
