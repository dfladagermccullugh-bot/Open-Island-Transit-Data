import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_site", ROOT / "scripts" / "check_site.py")
check_site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_site)

PAGE = """<!DOCTYPE html><html lang="en-US"><head>
<meta name="description" content="A description long enough to count.">
<meta property="og:title" content="A page">
<meta property="og:image" content="https://example.org/card.png">
<link rel="canonical" href="https://example.org/{name}.html">
{extra}</head><body><h1>Title</h1><a href="./routes.html#60">a route</a></body></html>"""
DATASET = '<script type="application/ld+json">{"@type": "Dataset"}</script>'


@pytest.fixture
def site(tmp_path):
    """A rendered site with everything the check asks for."""
    for page in check_site.PAGES:
        extra = DATASET if page == "index" else ""
        if page == "methods":
            extra = "".join(f'<h2 id="{a}">x</h2>' for a in check_site.METHOD_ANCHORS)
        (tmp_path / f"{page}.html").write_text(PAGE.format(name=page, extra=extra))
    for name in check_site.FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    for name in check_site.VENDORED:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / "site" / name).read_bytes())
    data = tmp_path / "data"
    (data / "ontime" / "recent.json").write_text(json.dumps({"timepoints": "p__t"}))
    (data / "maps").mkdir()
    (data / "maps" / "p__t.json").write_text("{}")
    for folder in ("ontime", "quality"):
        (data / folder / "daily.csv").write_text(
            "service_date,service_expected\n2026-11-25,true\n2026-11-26,false\n"
        )
        (data / folder / "days").mkdir()
        (data / folder / "days" / "2026-11-25.json").write_text("{}")
    return tmp_path


def test_a_complete_site_passes(site):
    assert check_site.problems(site) == []


def test_the_real_copies_of_the_map_library_are_the_published_files():
    for name, sha in check_site.VENDORED.items():
        import hashlib

        assert hashlib.sha256((ROOT / "site" / name).read_bytes()).hexdigest() == sha, name


def test_a_changed_copy_of_the_map_library_is_caught(site):
    (site / "vendor/leaflet-1.9.4/leaflet.js").write_text("alert(1)")
    assert any("not the published file" in p for p in check_site.problems(site))


@pytest.mark.parametrize("missing", ["charts.js", "page.js", "robots.txt", "data/build.json"])
def test_a_missing_file_is_named(site, missing):
    (site / missing).unlink()
    assert f"missing {missing}" in check_site.problems(site)


def test_the_latest_service_days_file_must_be_there_and_a_holiday_is_not_the_latest(site):
    # The holiday (2026-11-26) has no day file and none is asked for.
    assert check_site.problems(site) == []
    (site / "data/quality/days/2026-11-25.json").unlink()
    assert check_site.problems(site) == [
        "missing the latest day's file data/quality/days/2026-11-25.json"
    ]


def test_python_style_booleans_in_older_tables_are_still_read(site):
    (site / "data/ontime/daily.csv").write_text(
        "service_date,service_expected\n2026-11-25,True\n2026-11-26,False\n"
    )
    assert check_site.problems(site) == []


def test_a_missing_route_map_is_caught(site):
    (site / "data/maps/p__t.json").unlink()
    assert "missing the route map data/maps/p__t.json" in check_site.problems(site)


@pytest.mark.parametrize(
    ("remove", "expected"),
    [
        (
            '<meta name="description" content="A description long enough to count.">',
            "no description",
        ),
        ('lang="en-US"', "no page language"),
        ('<meta property="og:image" content="https://example.org/card.png">', "no preview image"),
        ('<link rel="canonical" href="https://example.org/routes.html">', "no canonical address"),
        ("<h1>Title</h1>", "expected exactly one top heading"),
    ],
)
def test_what_a_page_must_carry(site, remove, expected):
    page = site / "routes.html"
    page.write_text(page.read_text().replace(remove, ""))
    assert any(p == f"routes.html: {expected}" for p in check_site.problems(site))


def test_the_404_page_needs_no_canonical_address(site):
    page = site / "404.html"
    page.write_text(
        page.read_text().replace('<link rel="canonical" href="https://example.org/404.html">', "")
    )
    assert check_site.problems(site) == []


def test_a_link_to_a_page_that_does_not_exist_is_caught(site):
    (site / "routes.html").unlink()
    found = check_site.problems(site)
    assert "missing routes.html" in found
    assert "index.html: link to missing page routes.html" in found


def test_broken_structured_data_is_caught(site):
    (site / "index.html").write_text(
        PAGE.format(name="index", extra='<script type="application/ld+json">{oops</script>')
    )
    assert any("structured data is not valid JSON" in p for p in check_site.problems(site))


def test_the_home_page_must_describe_the_dataset(site):
    (site / "index.html").write_text(PAGE.format(name="index", extra=""))
    assert "index.html: no structured data describing the dataset" in check_site.problems(site)


def test_a_renamed_methods_heading_breaks_its_links(site):
    page = site / "methods.html"
    page.write_text(page.read_text().replace('id="validation"', 'id="checks"'))
    assert check_site.problems(site) == [
        "methods.html: no heading #validation, which the Methods links point to"
    ]


def _with_cell(site, page, code):
    path = site / f"{page}.html"
    path.write_text(path.read_text().replace("</body>", f"<script>{code}</script></body>"))


def test_a_page_drawing_charts_must_load_the_trimmed_bundle(site):
    _with_cell(site, "ontime", "ch = makeCharts({Plot}); Plot.plot({marks: [Plot.dot([])]})")
    assert check_site.problems(site) == [
        "ontime.html: uses Plot without importing vendor/plot-0.6.11/plot.js"
    ]


def test_a_plot_function_the_bundle_leaves_out_is_caught(site):
    _with_cell(site, "quality", 'import {Plot} from "./vendor/plot-0.6.11/plot.js"; '
               "Plot.plot({marks: [Plot.areaY([]), Plot.dot([])]})")  # fmt: skip
    problems = check_site.problems(site)
    assert len(problems) == 1 and "calls Plot.areaY" in problems[0]


def test_the_bundle_holds_what_its_entry_file_lists():
    have = check_site.bundled()
    assert {"plot", "dot", "tip", "lineY"} <= have["Plot"] and have["d3"] == {"csv", "autoType"}
