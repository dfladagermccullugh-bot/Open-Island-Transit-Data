"""Comparisons with other bus systems (``pipeline/compare.py``)."""

import json
import shutil
from pathlib import Path

from pipeline import compare

GOLDEN = Path(__file__).resolve().parent / "golden" / "data-branch"
# Minutes late of 100 departures: 10 early, 60 within 0 to 3, 20 within 3
# to 5, 10 later.
DEVS = [-120] * 10 + [60] * 60 + [240] * 20 + [600] * 10


def test_a_recount_under_another_rule_uses_its_window():
    assert compare.share_within(DEVS, 1, 5) == 0.8
    assert compare.share_within(DEVS, 2, 5) == 0.9
    assert compare.share_within(DEVS, 1, 3) == 0.6


def test_a_figure_under_our_own_rule_converts_to_itself():
    r = compare.converted_range([x + k for x in DEVS for k in range(0, 60, 7)], (1, 5), 0.8)
    assert r["low"] is not None and abs(r["low"] - 0.8) < 0.02 and abs(r["high"] - 0.8) < 0.02


def test_a_stricter_rule_converts_to_a_higher_share_under_ours():
    """80% on time within 3 minutes is better service than 80% within 5."""
    r = compare.converted_range(DEVS, (1, 3), 0.8)
    assert r["low"] is not None and r["low"] >= 0.8


def test_the_summary_labels_each_peer_with_its_classes(tmp_path):
    data = tmp_path / "data-branch"
    shutil.copytree(GOLDEN, data)
    peers = tmp_path / "peers.csv"
    peers.write_text(
        "agency,region,country,window_early_min,window_late_min,measured_moment,measured_where,"
        "cancellations,published_share,published_period,metric_note,source_urls,source_date,confidence\n"
        "Rule only,Washington,US,1,5,departure,timepoints,,,,,https://example.org,2026,low\n"
        "Rule and figure,Europe,UK,1,5,departure,timepoints,excluded,0.5,2025,,https://example.org,2026,high\n"
        "Headways,Asia,SG,,,,,,0.95,2025,excess wait time,https://example.org,2026,high\n"
    )
    out = compare.write(data, peers)
    saved = json.loads((data / "compare" / "summary.json").read_text())
    assert saved == out
    classes = {p["agency"]: p["classes"] for p in out["peers"]}
    assert classes == {"Rule only": ["R"], "Rule and figure": ["R", "B"], "Headways": []}
    assert out["island_transit"]["departures"] > 0
