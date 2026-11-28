"""Write the latest headline figure into the rendered site, before it is deployed.

    python scripts/prerender.py site/_site

Quarto renders the pages once, without data; the figures are drawn in the
reader's browser. A link preview, a search engine or a reader with
JavaScript off sees none of them. This step, run after ``quarto render``,
reads ``data/ontime/headline.json`` from the rendered site and:

1. puts the headline sentence ("74% of Island Transit's bus departures left
   on time ...") at the front of every page's ``description``,
   ``og:description`` and ``twitter:description``;
2. writes the number, its period and its counts into the home page's
   ``.slot-headline`` as plain HTML, which the page's own cell replaces once
   it has run (the stylesheet hides the plain copy then);
3. if ``tests/site/make-icons.mjs --card-from`` drew a link-preview card
   with that same figure (``social-card-figure.png`` and its ``.json``),
   renames it by its SHA-256, so a preview service that keeps old images
   asks for the new one, and points ``og:image`` and ``twitter:image`` at
   it. Otherwise the fixed card stays.

The numbers are formatted as ``site/lib.js`` formats them (``pct``,
``count``, ``longDate``), so the plain copy and the drawn one agree.
``scripts/check_site.py`` checks the result. Changes nothing when the site
has no headline file. Running it twice gives the same pages.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from html import escape, unescape
from pathlib import Path
from typing import Any

PRERENDER_VERSION = "prerender/0.1.0"
HEADLINE = Path("data") / "ontime" / "headline.json"
CARD = "social-card-figure.png"
CARD_INFO = "social-card-figure.json"
CARD_CURRENT = "social-card-current.json"
FIXED_CARD = "social-card.png"
WINDOW = "1 minute early to 5 minutes late"
# Marks the sentence this script put in front of a page's description, so a
# second run replaces it instead of adding another.
MARK = " "
SLOT = '<div class="slot-headline">'
STATIC_START = "<!-- prerendered headline -->"
STATIC_END = "<!-- /prerendered headline -->"


def pct(x: float, digits: int = 0) -> str:
    """``pct`` of site/lib.js: ``(100 * x).toFixed(digits)``, halves away
    from zero as JavaScript rounds them (Python rounds them to even)."""
    q = Decimal(1).scaleb(-digits)
    text = str(Decimal(100 * x).quantize(q, rounding=ROUND_HALF_UP))
    return f"{text.replace('-', '') if float(text) == 0 else text}%"


def count(n: int) -> str:
    return f"{int(n):,}"


def long_date(value: str) -> str:
    d = date.fromisoformat(value)
    return f"{d:%B} {d.day}, {d.year}"


def period(head: dict[str, Any]) -> str:
    """The period words of the home headline (site/index.qmd, ``period``)."""
    days, last = head["current"]["days"], long_date(head["last_service_date"])
    return f"on {last}" if days == 1 else f"over the last {days} days with service, to {last}"


def figure(head: dict[str, Any] | None) -> dict[str, str] | None:
    """The pieces of the headline, or None when there is no figure to show."""
    c = (head or {}).get("current") or {}
    if not c.get("observed") or c.get("on_time_share_of_observed") is None:
        return None
    assert head is not None
    share = pct(c["on_time_share_of_observed"])
    when = period(head)
    return {
        "share": share,
        "period": when,
        "last_service_date": head["last_service_date"],
        "sentence": (f"{share} of Island Transit's bus departures left on time ({WINDOW}) {when}."),
        "counts": (
            f"That is {count(c['on_time'])} of the {count(c['observed'])} departures "
            f"we could time, out of {count(c['scheduled'])} scheduled."
        ),
    }


def static_block(fig: dict[str, str]) -> str:
    """The plain copy of the headline, in the same markup the cell draws, so
    it looks the same and the page does not move when the cell replaces it."""
    return (
        f'{STATIC_START}<div class="headline-block headline-static"><div class="headline">'
        f'<div class="big">{escape(fig["share"])}</div>'
        f'<div class="big-text"><p class="big-label">of departures left on time '
        f"{escape(fig['period'])}."
        f' <span class="note">A bus counts as on time if it leaves from {WINDOW}.</span></p>'
        f'<p class="counts">{escape(fig["counts"])}</p></div></div></div>{STATIC_END}'
    )


def content(html: str, attr: str, name: str) -> str | None:
    m = re.search(rf'<meta {attr}="{re.escape(name)}" content="([^"]*)"', html)
    return m.group(1) if m else None


def _attr(value: str) -> str:
    return escape(value, quote=False).replace('"', "&quot;")


def _set(html: str, attr: str, name: str, value: str) -> str:
    pattern = rf'(<meta {attr}="{re.escape(name)}" content=")[^"]*(")'
    return re.sub(pattern, lambda m: m.group(1) + _attr(value) + m.group(2), html)


def own_description(text: str) -> str:
    """A page's description without a sentence an earlier run put in front."""
    earlier = r"^\d+(?:\.\d+)?% of Island Transit's bus departures left on time .*?, \d{4}\. "
    return re.sub(earlier, "", text)


def _same(meta: dict[str, Any], fig: dict[str, str]) -> bool:
    return meta.get("share") == fig["share"] and meta.get("period") == fig["period"]


def card(site: Path, fig: dict[str, str]) -> tuple[str, str] | None:
    """(file name, alt text) of the drawn card, renamed by its hash, or None
    when no card was drawn or it shows another figure than the headline."""
    png, info, current = site / CARD, site / CARD_INFO, site / CARD_CURRENT
    if not png.exists() or not info.exists():
        # A second run: the card was renamed by the first.
        if current.exists():
            meta = json.loads(current.read_text(encoding="utf-8"))
            if (site / meta["file"]).exists() and _same(meta, fig):
                return meta["file"], meta["alt"]
        return None
    meta = json.loads(info.read_text(encoding="utf-8"))
    if not _same(meta, fig):
        print(
            f"prerender: the card shows {meta.get('share')} {meta.get('period')}, "
            f"the headline {fig['share']} {fig['period']}; keeping the fixed card",
            file=sys.stderr,
        )
        return None
    sha = hashlib.sha256(png.read_bytes()).hexdigest()[:12]
    name = f"social-card-{sha}.png"
    png.replace(site / name)
    info.unlink()
    alt = fig["sentence"]
    current.write_text(
        json.dumps({"file": name, "alt": alt, "share": fig["share"], "period": fig["period"]})
        + "\n",
        encoding="utf-8",
    )
    return name, alt


def prerender_page(html: str, fig: dict[str, str], image: tuple[str, str] | None) -> str:
    for attr, name in (
        ("name", "description"),
        ("property", "og:description"),
        ("name", "twitter:description"),
    ):
        old = content(html, attr, name)
        if old is None:
            continue
        mine = own_description(unescape(old))
        html = _set(html, attr, name, f"{fig['sentence']}{MARK}{mine}")
    if image is not None:
        file, alt = image
        for attr, name in (("property", "og:image"), ("name", "twitter:image")):
            old = content(html, attr, name)
            if old is not None:
                html = _set(html, attr, name, old.rsplit("/", 1)[0] + "/" + file)
        for attr, name in (("property", "og:image:alt"), ("name", "twitter:image:alt")):
            html = _set(html, attr, name, alt)
    if SLOT in html:
        old_block = "\n" + re.escape(STATIC_START) + ".*?" + re.escape(STATIC_END)
        html = re.sub(old_block, "", html, flags=re.S)
        html = html.replace(SLOT, SLOT + "\n" + static_block(fig), 1)
    return html


def prerender(site: Path) -> int:
    """Rewrites the pages of a rendered site; returns how many changed."""
    path = site / HEADLINE
    if not path.exists():
        print("prerender: no headline file, pages left as rendered")
        return 0
    fig = figure(json.loads(path.read_text(encoding="utf-8")))
    if fig is None:
        print("prerender: the headline has no figure yet, pages left as rendered")
        return 0
    image = card(site, fig)
    changed = 0
    for page in sorted(site.glob("*.html")):
        text = page.read_text(encoding="utf-8")
        new = prerender_page(text, fig, image)
        if new != text:
            page.write_text(new, encoding="utf-8")
            changed += 1
    which = image[0] if image else FIXED_CARD
    print(f"prerender: “{fig['sentence']}” ({changed} pages changed); preview image {which}")
    return changed


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    prerender(Path(sys.argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
