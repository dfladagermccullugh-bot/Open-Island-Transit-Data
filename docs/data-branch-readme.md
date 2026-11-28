# Data branch

The nightly job of [Open Island Transit Data](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data) writes this branch.
Nothing here is edited by hand, and nothing arrives here unchecked: the
jobs write to the `data-staging` branch first, and this branch moves forward
to what they wrote only when every rule of the publishing gate passes. The
gate's report is `checks/gate.json`. The large files (the raw archive and the
parsed tables) are in the repository's [Releases](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/releases); this
branch holds the small ones and the lists of what the Releases contain.

| File or folder | Contents |
|---|---|
| `files.csv` | Every file in the Releases, one row each: its service day, the table it holds, its plain name, its name in the Release (for parsed tables and stop events uploaded since November 28, 2026, the plain name with the first 8 characters of its SHA-256 added), its size, its SHA-256 and the address to download it from |
| `checks/` | `gate.json`: the report of the publishing gate this branch passed (the rules, what they found, the commit checked and when). `gate-light.json`: the same for the light gate run when comparisons or validator reports are added during the day |
| `datapackage.json` | Every table and column, with types and units, in the [Data Package](https://datapackage.org) format that R, Python and other tools can read |
| `manifest/` | `raw.json`, `parsed.json` and `events.json` list every archived service day with its files in Releases, their sizes and SHA-256 hashes. `static.json` lists the schedule versions and the one pinned to each day |
| `quality/` | Daily feed-quality figures: one JSON file per day, `daily.csv` with the headline figures, and `gaps.json` on whether missed trips happen at random |
| `ontime/` | Daily on-time summaries: one JSON file per day, `daily.csv`, `routes.csv`, the 28-day route tables in `recent.json`, and the field check in `field-check.json` |
| `timepoints/` | The stops printed in Island Transit's timetables, matched to the schedule's stops |
| `crosswalk/` | Links between the Passio and Trillium schedule files |
| `maps/` | Route lines and timepoint stops for the site's map |
| `reports/` | One report for each complete month |

Every file and column is described in the [codebook](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/blob/main/docs/codebook.md),
and every definition in the [methods](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/blob/main/docs/methods.md). The
terms of use are in [LICENSE-DATA.md](https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data/blob/main/LICENSE-DATA.md).
