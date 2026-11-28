import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prerender  # noqa: E402

spec = importlib.util.spec_from_file_location("check_site", ROOT / "scripts" / "check_site.py")
check_site = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_site)

HEAD = {
    "last_service_date": "2026-11-27",
    "current": {
        "days": 28,
        "scheduled": 15000,
        "observed": 13095,
        "on_time": 9719,
        "on_time_share_of_observed": 0.7422,
    },
}
PAGE = """<!DOCTYPE html><html lang="en-US"><head>
<meta name="description" content="How often Island Transit’s buses run on time.">
<meta property="og:description" content="How often Island Transit’s buses run on time.">
<meta property="og:image" content="https://example.org/social-card.png">
<meta property="og:image:alt" content="Open Island Transit Data">
<meta name="twitter:description" content="How often Island Transit’s buses run on time.">
<meta name="twitter:image" content="https://example.org/social-card.png">
<meta name="twitter:image:alt" content="Open Island Transit Data">
</head><body><div class="slot-headline">
<div class="cell"></div></div></body></html>"""


def site(tmp_path: Path, head=HEAD) -> Path:
    (tmp_path / "data" / "ontime").mkdir(parents=True)
    (tmp_path / "data" / "ontime" / "headline.json").write_text(json.dumps(head))
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "social-card.png").write_bytes(b"fixed")
    return tmp_path


def test_pct_rounds_halves_as_javascript_does():
    # (100 * 0.625).toFixed(0) is "63" in JavaScript; Python's format gives 62.
    assert prerender.pct(0.625) == "63%"
    assert prerender.pct(0.7422) == "74%"
    assert prerender.pct(0.004) == "0%"
    assert prerender.pct(0.70095, 1) == "70.1%"


def test_sentence_and_period():
    fig = prerender.figure(HEAD)
    assert fig["sentence"] == (
        "74% of Island Transit's bus departures left on time (1 minute early to "
        "5 minutes late) over the last 28 days with service, to November 27, 2026."
    )
    one = {**HEAD, "current": {**HEAD["current"], "days": 1}}
    assert prerender.figure(one)["period"] == "on November 27, 2026"
    assert prerender.figure({"current": {"observed": 0}}) is None
    assert prerender.figure(None) is None


def test_pages_carry_the_figure_and_a_second_run_changes_nothing(tmp_path):
    s = site(tmp_path)
    assert prerender.prerender(s) == 1
    html = (s / "index.html").read_text(encoding="utf-8")
    assert '<meta name="description" content="74% of Island Transit' in html
    assert "2026. How often Island Transit’s buses run on time." in html
    assert '<div class="big">74%</div>' in html
    assert "That is 9,719 of the 13,095 departures we could time, out of 15,000 scheduled." in html
    # No card was drawn: the fixed one stays.
    assert 'content="https://example.org/social-card.png"' in html
    assert prerender.prerender(s) == 0
    assert (s / "index.html").read_text(encoding="utf-8") == html
    assert check_site.prerender_problems(s) == []


def test_a_drawn_card_is_named_by_its_hash(tmp_path):
    s = site(tmp_path)
    fig = prerender.figure(HEAD)
    (s / prerender.CARD).write_bytes(b"card with 74%")
    (s / prerender.CARD_INFO).write_text(json.dumps({"share": "74%", "period": fig["period"]}))
    prerender.prerender(s)
    cards = sorted(p.name for p in s.glob("social-card-*.png"))
    assert len(cards) == 1 and cards[0] != prerender.CARD
    html = (s / "index.html").read_text(encoding="utf-8")
    assert f'<meta property="og:image" content="https://example.org/{cards[0]}">' in html
    assert f'<meta name="twitter:image:alt" content="{fig["sentence"]}">' in html
    # A second run finds the renamed card and leaves the pages as they are.
    assert prerender.prerender(s) == 0
    assert check_site.prerender_problems(s) == []


def test_a_card_with_another_figure_is_not_used(tmp_path):
    s = site(tmp_path)
    (s / prerender.CARD).write_bytes(b"old card")
    (s / prerender.CARD_INFO).write_text(json.dumps({"share": "71%", "period": "on May 1, 2026"}))
    prerender.prerender(s)
    assert 'content="https://example.org/social-card.png"' in (s / "index.html").read_text()


def test_the_site_check_catches_a_stale_description(tmp_path):
    s = site(tmp_path)
    prerender.prerender(s)
    page = s / "index.html"
    page.write_text(page.read_text().replace("74% of Island", "73% of Island"))
    found = check_site.prerender_problems(s)
    assert any("description does not open with the figure 74%" in f for f in found)


def test_no_headline_leaves_the_pages_alone(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    assert prerender.prerender(tmp_path) == 0
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == PAGE
    assert check_site.prerender_problems(tmp_path) == []
