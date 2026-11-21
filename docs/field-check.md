# Field check: timing buses in person

Check 1 of the validation (`docs/methods.md`, section 5): a spot check of
whether the inferred stop times match what happens on the street. The target is a
mean absolute difference of 30 seconds or less between the timed and the
inferred times, judged once at least five moments have been timed. Done on route 60, the route the observer rides; more
timings on any trip can be added at any time.

## Before going out

1. Pick a weekday, one or two timepoints, and a time window of an hour or two.
   A busy hub (Harbor Station, Clinton Ferry Terminal) gives many buses in a
   short time; a quieter through-stop gives cleaner timings.
2. Get a sheet listing every scheduled bus there, with trip ids filled in:

   ```bash
   python -m pipeline.events.field sheet --date 2026-11-23 --stop "Clinton Ferry" --from 07:00 --to 09:00 > sheet.csv
   ```

3. Use a clock that shows seconds and is set from the network, for example a
   phone's clock or time.gov.

## What to time

The method infers two moments for each bus at a stop, and the observer times
the same two:

- `arrive_40m`: the front of the bus comes within **40 m** of the stop
  (about three bus lengths, or half a short block) on its way in.
- `depart_40m`: the front of the bus is more than **40 m** past the stop on
  its way out.

From on board, or when the 40 m points are hard to judge, time instead:

- `pass_stop`: the bus is level with the stop. This is compared with the
  midpoint of the inferred arrival and departure.

`time_this` on the sheet says which apply: `departure` at the stop where the
trip starts, `arrival` where it ends, `both` elsewhere. Write times as
`14:03:27` or `2:03:27 pm` (seconds are required). Leave a time blank if you
missed it; use `note` for anything odd (bus did not stop, wrong route sign,
two buses at once).

If a bus comes that is not on the sheet, add a row with the route and time in
`note` and leave `trip_id` blank; it will be reported but not scored.

## Afterwards

Send the filled sheet (a photo is fine) or the CSV. The rows go into
`reference/field-observations.csv`. The nightly job scores them once their
day has been processed and writes `ontime/field-check.json`; results appear
on the site's on-time page under "Field check".
