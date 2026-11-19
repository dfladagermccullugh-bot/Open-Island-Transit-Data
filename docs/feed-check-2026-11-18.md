# Feed check, 2026-11-18 (after service hours)

First check of the feeds, run 22:50–23:30 Pacific on Wednesday 2026-11-18.
Island Transit's last scheduled trip ends at 20:25, so no buses were in
service. The daytime check ran the next morning; its report is
[`feed-check-daytime.md`](feed-check-daytime.md), written by
`scripts/verify_feed.py`.

## Results

| # | Check | Result |
|---|---|---|
| 1 | Fetch the three real-time endpoints | HTTP 200 for all three, 15 bytes each (header only, no entities). An unknown agency slug on the same host returns 404, so "200 and empty" means the feed is up with no buses. |
| 2 | Decode | Valid GTFS-Realtime 2.0, `FULL_DATASET`, 0 entities. |
| 3 | Header timestamp | Equals the request time on every poll (age under 1 s). It therefore cannot show that a feed is frozen. Staleness has to be measured from per-vehicle timestamps. |
| 4 | Caching | The `ETag` changes on every request, so conditional requests are not useful. |
| 5 | Rate limit | Response headers report a limit of 6,000 requests per window (about one minute) per IP address. Polling two feeds every 20 s uses 6 per minute. |
| 6 | Static GTFS from Passio | 17 routes, 677 trips, 474 stops, 49 shapes; numeric IDs; block IDs on every trip. `feed_info.txt`: version `20260917`, dates 2026-11-17 to 2026-12-18. Transitland lists a new version every day (rolling 31-day window). |
| 7 | Static GTFS zip integrity | In five downloads the Passio zip ended after `shapes.txt`: no `calendar.txt`, no `calendar_dates.txt`, and no central directory. Standard zip tools reject it. The seven entries that arrived are intact (recovered by `pipeline/gtfs/zipread.py`). The same endpoint for another agency returned a complete zip at the same time. |
| 8 | Static GTFS from Trillium | Complete zip, 24 files, last modified 2026-09-29, valid to 2027-04-01. 16 routes, 465 trips, calendar with holidays. Uses a different ID scheme from Passio. |
| 9 | Timepoints | Passio marks every stop `timepoint=1`; all times fall on whole minutes and 22% of consecutive stops share the same time, so most are interpolated. Trillium marks 3,438 of 12,291 stop-times as timepoints. 386 of 410 Passio stop codes also appear in Trillium. |
| 10 | Terms of use | None found for the feeds. Passio `feed_info.txt` has no license field; Trillium `feed_license` is blank; the Transitland Atlas record lists none; Island Transit's website has general website terms only. |

## What other Passio feeds look like

Island Transit's feed was empty, so four other agencies on the same host that
had vehicles reporting were decoded to learn the format. This is evidence
about the vendor's format, not about Island Transit.

- Vehicle positions carry: trip ID, route ID, latitude, longitude, bearing,
  current stop sequence, stop ID, vehicle ID and label, timestamp, and
  `occupancy_status`. Speed appears on some vehicles only.
- Not present: `start_date`, `start_time`, `current_status`.
- Vehicle timestamps were 3 to 9 seconds old at receipt and changed on every
  poll at 12-second spacing.
- Trip updates give predicted arrival and departure as clock times. No
  `delay` field. Two of the four agencies published no trip updates while
  vehicles were reporting.
- For one agency, real-time trip, route and stop IDs all matched that
  agency's Passio static GTFS.

## Service hours

From islandtransit.org: Whidbey Monday–Friday 3:45 AM–7:50 PM, weekends
6:45 AM–7:00 PM; Camano Monday–Friday 5:45 AM–8:00 PM, weekends
7:30 AM–6:00 PM. In the schedule no trip runs past midnight, so the service
date is the Pacific calendar date.

## Answered by the daytime check (2026-11-19, 07:31 Pacific)

- **Fields:** every vehicle carried vehicle ID and label, trip ID, route ID,
  position, bearing, speed, stop sequence, stop ID, timestamp and occupancy.
  Never sent: `start_date`, `start_time`, `direction_id`, `current_status`.
- **Trip updates:** published, as predicted clock times with no `delay`
  field. 1 of 544 stop predictions equalled the schedule, so they are real
  predictions. Their content changed on 4 of 11 polls at 20-second spacing.
- **ID match:** all real-time trip, route and stop IDs were found in the
  Passio static GTFS.
- **Static zip:** complete at 07:40 Pacific, with `calendar.txt` (no
  `calendar_dates.txt`). The incomplete zip seen the night before was
  therefore intermittent. Every download is still checked.
