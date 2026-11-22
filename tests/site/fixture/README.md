# The data the site tests run on

A copy of the files the site reads (what `scripts/site_data.py` copies from
the `data` branch), as published for service days November 19 and 20, 2026.
The browser tests in `tests/site/` render the site with these files, so a
test that says "Route 60 was on time 34% of the time" keeps meaning the same
thing after newer days are published.

`tests/site/scenarios.mjs` changes these files on the fly to stand in for a
holiday, a first day, a long history, and missing values or files.

To refresh the copy (expect to update the figures the tests name):

    python scripts/site_data.py <data branch folder> tests/site/fixture/data
