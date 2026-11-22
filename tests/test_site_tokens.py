"""The site's colors keep their contrast, and the on-time bands stay apart.

The two theme files (``site/styles.scss`` and ``site/styles-dark.scss``) name
every color the pages use. This test reads the names straight from those
files and checks three promises, so that a later change of palette cannot
quietly break them:

1. Text can be read: every text color has a WCAG contrast of at least 4.5 to 1
   against the surface, the card and the panel.
2. Marks can be seen: every color that carries meaning in a chart or on the
   map has at least 3 to 1 against the same backgrounds (WCAG 1.4.11).
3. The four on-time bands can be told apart by readers with the three common
   kinds of color blindness. The bands also differ in shape, but the colors
   alone should not collapse into one.
"""

import itertools
import re
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1] / "site"
THEMES = {"light": SITE / "styles.scss", "dark": SITE / "styles-dark.scss"}
BACKGROUNDS = ("surface", "card", "panel")
TEXT = ("text-primary", "text-secondary", "text-muted", "link")
MARKS = ("series-1", "rule", "range", "focus", "status-good", "status-warning",
         "status-critical", "status-none")  # fmt: skip
BANDS = ("status-good", "status-warning", "status-critical", "status-none")
# The least difference (CIE76) between any two bands under simulated color
# blindness. About 10 is where two colors side by side start to look alike.
MIN_BAND_DISTANCE = 15

# How colors appear with no working L, M or S cones (Machado, Oliveira and
# Fernandes, 2009, severity 1.0), applied to linear RGB.
VISION = {
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216),
                   (-0.003882, -0.048116, 1.051998)),
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413),
                     (-0.011820, 0.042940, 0.968881)),
    "tritanopia": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602),
                   (0.004733, 0.691367, 0.303900)),
}  # fmt: skip


def tokens(path: Path) -> dict[str, str]:
    """The ``--name: #rrggbb`` pairs in a theme file's ``:root`` block."""
    root = re.search(r":root\s*\{(.*?)\}", path.read_text(encoding="utf-8"), re.S)
    assert root, f"{path.name} has no :root block"
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", root.group(1)))


def linear(hex_color: str) -> tuple[float, float, float]:
    channels = (int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)


def luminance(hex_color: str) -> float:
    r, g, b = linear(hex_color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (min(1.0, max(0.0, c)) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    fx, fy, fz = (t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116 for t in (x, y, z))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def seen_as(hex_color: str, vision: str) -> tuple[float, float, float]:
    rgb = linear(hex_color)
    return lab(tuple(sum(m * c for m, c in zip(row, rgb, strict=True)) for row in VISION[vision]))


def distance(a: str, b: str, vision: str) -> float:
    return (
        sum((p - q) ** 2 for p, q in zip(seen_as(a, vision), seen_as(b, vision), strict=True))
        ** 0.5
    )


@pytest.mark.parametrize("theme", THEMES)
def test_both_themes_name_the_same_colors(theme):
    assert set(tokens(THEMES[theme])) == set(tokens(THEMES["light"]))


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("name", TEXT)
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_text_can_be_read(theme, name, background):
    t = tokens(THEMES[theme])
    assert contrast(t[name], t[background]) >= 4.5, (theme, name, background)


@pytest.mark.parametrize("theme", THEMES)
def test_links_can_be_read_on_the_tinted_panel(theme):
    t = tokens(THEMES[theme])
    assert contrast(t["link"], t["panel-accent"]) >= 4.5
    assert contrast(t["text-secondary"], t["panel-accent"]) >= 4.5


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("name", MARKS)
@pytest.mark.parametrize("background", BACKGROUNDS)
def test_marks_can_be_seen(theme, name, background):
    t = tokens(THEMES[theme])
    assert contrast(t[name], t[background]) >= 3.0, (theme, name, background)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("vision", VISION)
def test_bands_stay_apart_for_color_blind_readers(theme, vision):
    t = tokens(THEMES[theme])
    closest = min(distance(t[a], t[b], vision) for a, b in itertools.combinations(BANDS, 2))
    assert closest >= MIN_BAND_DISTANCE, (theme, vision, round(closest, 1))


def test_the_theme_passed_to_bootstrap_matches_the_named_link_color():
    for path in THEMES.values():
        text = path.read_text(encoding="utf-8")
        link = re.search(r"\$link-color:\s*(#[0-9a-fA-F]{6})", text).group(1)
        assert link.lower() == tokens(path)["link"].lower()


def test_contrast_of_black_on_white_is_21():
    assert round(contrast("#000000", "#ffffff"), 1) == 21.0
