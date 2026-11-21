"""Check a rendered site before it is deployed.

    python scripts/check_site.py site/_site

Exits 1 if a page or a data file a page loads is missing, if a page has no
description, or if a link to another page of the site points nowhere.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PAGES = ("index", "routes", "ontime", "quality", "data", "methods", "codebook", "glossary", "404")
FILES = (
    "lib.js",
    "map.js",
    "favicon.svg",
    "social-card.png",
    "sitemap.xml",
    "data/build.json",
    "data/ontime/recent.json",
    "data/ontime/daily.csv",
    "data/quality/daily.csv",
    "data/manifest/raw.json",
)


def problems(site: Path) -> list[str]:
    out = [f"missing {f}" for f in FILES if not (site / f).exists()]
    for page in PAGES:
        path = site / f"{page}.html"
        if not path.exists():
            out.append(f"missing {page}.html")
            continue
        html = path.read_text(encoding="utf-8")
        if not re.search(r'<meta name="description" content="[^"]{20,}"', html):
            out.append(f"{page}.html: no description")
        if len(re.findall(r"<h1[\s>]", html)) != 1:
            out.append(f"{page}.html: expected exactly one top heading")
        for href in re.findall(r'href="(?:\./)?([a-z0-9-]+\.html)(?:#[^"]*)?"', html):
            if not (site / href).exists():
                out.append(f"{page}.html: link to missing page {href}")
    recent = site / "data" / "ontime" / "recent.json"
    if recent.exists():
        key = json.loads(recent.read_text(encoding="utf-8")).get("timepoints")
        if key and not (site / "data" / "maps" / f"{key}.json").exists():
            out.append(f"missing the route map data/maps/{key}.json")
    return out


def main() -> int:
    found = problems(Path(sys.argv[1]))
    for p in found:
        print(p, file=sys.stderr)
    print(f"site check: {len(found)} problems")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
