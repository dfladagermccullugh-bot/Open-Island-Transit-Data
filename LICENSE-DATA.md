# Data license and source

## Source

The data in this project comes from Island Transit's public GTFS and
GTFS-Realtime feeds, hosted by Passio Technologies:

- `https://passio3.com/islandtransit/passioTransit/gtfs/realtime/vehiclePositions`
- `https://passio3.com/islandtransit/passioTransit/gtfs/realtime/tripUpdates`
- `https://passio3.com/islandtransit/passioTransit/gtfs/realtime/serviceAlerts`
- `https://passio3.com/islandtransit/passioTransit/gtfs/google_transit.zip`
- `https://data.trilliumtransit.com/gtfs/islandtransit-wa-us/islandtransit-wa-us.zip`

As of 2026-11-18 no license or terms of use were published for these feeds:
the Passio `feed_info.txt` names none, the Trillium `feed_license` field is
blank, and the Transitland Atlas record lists none. Island Transit's website
has general website terms but no developer or data page. See
`docs/feed-check-2026-11-18.md`.

## What this project licenses

- **Raw archive** (the feed responses exactly as received): republished as
  received. This project claims no rights in it.
- **Derived tables** (fetch log, parsed tables, inferred stop events, quality
  metrics, on-time measures): Creative Commons Attribution 4.0 International
  (CC BY 4.0), https://creativecommons.org/licenses/by/4.0/

Suggested attribution: "Open Island Transit Data, derived from Island
Transit's public GTFS feeds."

## Not affiliated

This is an independent project. It is not affiliated with, endorsed by, or
checked by Island Transit or Passio Technologies. Derived measures such as
inferred arrival times are estimates with documented error, not official
performance figures.

## Contact

Questions or objections, including requests to remove data: open an issue at
https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/issues
