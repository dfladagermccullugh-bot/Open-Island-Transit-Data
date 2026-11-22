#!/usr/bin/env bash
# Build the site and test it in a browser.
#
#   scripts/test_site.sh         Every test, at a desk and on a phone, light
#                                and dark, on the kept copy of the data in
#                                tests/site/fixture/, so results do not change
#                                when new days are published.
#   scripts/test_site.sh smoke   Only "every page runs clean", on the site
#                                already rendered in site/_site (the one about
#                                to be deployed, built from the live data).
#
# Needs Python with quarto-cli 1.11.9 and Node. On a machine without the
# browser (GitHub's runners), set PLAYWRIGHT_INSTALL=1 to download it.
set -euo pipefail
cd "$(dirname "$0")/.."

what="${1:-all}"

if [ "$what" != "smoke" ]; then
  rm -rf site/data
  cp -r tests/site/fixture/data site/data
  quarto render site
fi
python scripts/check_site.py site/_site

cd tests/site
npm ci --no-audit --no-fund
if [ -n "${PLAYWRIGHT_INSTALL:-}" ]; then
  npx playwright install --with-deps chromium
fi
if [ "$what" = "smoke" ]; then
  npx playwright test smoke.spec.mjs --project=desktop-light --project=phone-dark
else
  npx playwright test
fi
