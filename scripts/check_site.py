"""Check a rendered site before it is deployed.

    python scripts/check_site.py site/_site

Exits 1 if a page or a file a page loads is missing, if a page lacks what a
search engine or a link preview reads (description, canonical address, title
and image for previews), if the page language is not set, if a link to
another page of the site points nowhere, or if the map library copied into
the site is not byte for byte the published one.

This reads the files only. Whether the pages run is checked in a browser by
the tests in tests/site/.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

# Headings of the Methods page that figures link to ("How we measured this").
# Quarto makes each id from the heading's words, so renaming a heading breaks
# its links; this list makes that fail the site check.
METHOD_ANCHORS = ("collection", "feed-quality-metrics", "arrival-and-departure-times",
                  "on-time-performance", "validation", "comparisons")  # fmt: skip
PAGES = (
    "index",
    "routes",
    "ontime",
    "quality",
    "data",
    "methods",
    "codebook",
    "glossary",
    "limits",
    "compare",
    "stops",
    "replay",
    "404",
)
FILES = (
    "lib.js",
    "charts.js",
    "map.js",
    "page.js",
    "favicon.svg",
    "favicon-32.png",
    "apple-touch-icon.png",
    "social-card.png",
    "sitemap.xml",
    "robots.txt",
    "data/build.json",
    "data/ontime/recent.json",
    "data/ontime/daily.csv",
    "data/quality/daily.csv",
    "data/manifest/raw.json",
)
# Leaflet 1.9.4 as published on npm (dist/leaflet.js and dist/leaflet.css).
# The site serves its own copy, so the copy is pinned here.
VENDORED = {
    "vendor/leaflet-1.9.4/leaflet.js": (
        "db49d009c841f5ca34a888c96511ae936fd9f5533e90d8b2c4d57596f4e5641a"
    ),
    "vendor/leaflet-1.9.4/leaflet.css": (
        "a7837102824184820dfa198d1ebcd109ff6d0ff9a2672a074b9a1b4d147d04c6"
    ),
    # The chart library: the parts of Observable Plot 0.6.11 and d3 7.8.5 the
    # pages use, built by tests/site/make-plot.mjs (rerun it, then update these).
    "vendor/plot-0.6.11/plot.js": (
        "dc5f1cb2ac893763890f117a5ab3c70924a45d6ca50f1215c8bbf893a76b59e1"
    ),
    "vendor/plot-0.6.11/LICENSE": (
        "2c3b44516eaa6a3ef8964ac46d7db232807cf04afc48937b4d5913c8b6c7b14b"
    ),
}
# The functions that bundle holds, as its entry file lists them.
PLOT_ENTRY = Path(__file__).resolve().parents[1] / "tests" / "site" / "build" / "plot-entry.js"


def bundled(entry: Path = PLOT_ENTRY) -> dict[str, set[str]]:
    """``{"Plot": {...}, "d3": {...}}``: the names each export of the bundle holds."""
    text = entry.read_text(encoding="utf-8")
    out = {}
    for name in ("Plot", "d3"):
        m = re.search(r"export const " + name + r" = \{([^}]*)\}", text)
        out[name] = set(re.findall(r"[A-Za-z]+", m.group(1))) if m else set()
    return out


def chart_problems(site: Path) -> list[str]:
    """A page that draws with Plot or reads with d3 loads the trimmed bundle,
    and calls only what the bundle holds; otherwise it would fail, or fall
    back to the full libraries from the CDN."""
    have = bundled()
    out = []
    for page in sorted(site.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        for lib in ("Plot", "d3"):
            called = set(re.findall(rf"\b{lib}\.([A-Za-z]+)\(", text))
            if not called:
                continue
            if "vendor/plot-0.6.11/plot.js" not in text:
                out.append(f"{page.name}: uses {lib} without importing vendor/plot-0.6.11/plot.js")
            for fn in sorted(called - have[lib]):
                out.append(
                    f"{page.name}: calls {lib}.{fn}, which the chart bundle does not hold "
                    "(add it to tests/site/build/plot-entry.js and rerun make-plot.mjs)"
                )
    return out


HEAD = {
    "description": r'<meta name="description" content="[^"]{20,}"',
    "page language": r'<html[^>]*\blang="en-US"',
    "preview title": r'<meta property="og:title" content="[^"]+"',
    "preview image": r'<meta property="og:image" content="https://[^"]+"',
}


def latest_service_day(daily: Path) -> str | None:
    """The last day in a daily table on which buses were scheduled."""
    with daily.open(encoding="utf-8", newline="") as fh:
        days = [
            r["service_date"]
            for r in csv.DictReader(fh)
            if r.get("service_expected", "true").strip().lower() != "false"
        ]
    return days[-1] if days else None


def page_problems(site: Path, page: str) -> list[str]:
    path = site / f"{page}.html"
    if not path.exists():
        return [f"missing {page}.html"]
    html = path.read_text(encoding="utf-8")
    out = [
        f"{page}.html: no {what}" for what, pattern in HEAD.items() if not re.search(pattern, html)
    ]
    if page != "404" and not re.search(r'<link rel="canonical" href="https://[^"]+"', html):
        out.append(f"{page}.html: no canonical address")
    if len(re.findall(r"<h1[\s>]", html)) != 1:
        out.append(f"{page}.html: expected exactly one top heading")
    for href in re.findall(r'href="(?:\./)?([a-z0-9-]+\.html)(?:#[^"]*)?"', html):
        if not (site / href).exists():
            out.append(f"{page}.html: link to missing page {href}")
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            json.loads(block)
        except ValueError as exc:
            out.append(f"{page}.html: structured data is not valid JSON ({exc})")
    return out


def data_problems(site: Path) -> list[str]:
    """The files the pages ask for by a name they work out from other files."""
    out = []
    data = site / "data"
    recent = data / "ontime" / "recent.json"
    if recent.exists():
        key = json.loads(recent.read_text(encoding="utf-8")).get("timepoints")
        if key and not (data / "maps" / f"{key}.json").exists():
            out.append(f"missing the route map data/maps/{key}.json")
    for folder in ("ontime", "quality"):
        daily = data / folder / "daily.csv"
        day = latest_service_day(daily) if daily.exists() else None
        if day and not (data / folder / "days" / f"{day}.json").exists():
            out.append(f"missing the latest day's file data/{folder}/days/{day}.json")
    return out


def problems(site: Path) -> list[str]:
    out = [f"missing {f}" for f in (*FILES, *VENDORED) if not (site / f).exists()]
    for name, sha in VENDORED.items():
        path = site / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            out.append(f"{name} is not the published file (its SHA-256 differs)")
    for page in PAGES:
        out += page_problems(site, page)
    home = site / "index.html"
    if home.exists() and "application/ld+json" not in home.read_text(encoding="utf-8"):
        out.append("index.html: no structured data describing the dataset")
    methods = site / "methods.html"
    if methods.exists():
        text = methods.read_text(encoding="utf-8")
        for a in METHOD_ANCHORS:
            if f'id="{a}"' not in text and f'data-anchor-id="{a}"' not in text:
                out.append(f"methods.html: no heading #{a}, which the Methods links point to")
    return out + chart_problems(site) + data_problems(site)


def main() -> int:
    found = problems(Path(sys.argv[1]))
    for p in found:
        print(p, file=sys.stderr)
    print(f"site check: {len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
