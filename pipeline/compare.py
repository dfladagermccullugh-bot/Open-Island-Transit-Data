"""Fair comparisons with other bus systems (docs/methods.md, section 6).

    python -m pipeline.compare --data data-branch [--peers reference/peers/definitions.csv]

Agencies measure on-time performance in different ways: one counts a bus up
to 5 minutes late, another up to 3; one times departures at timepoints,
another arrivals at every stop. Two published percentages are therefore not
comparable as they stand. This stage puts them on a common footing in two
ways, each labeled:

- R, exact reverse: Island Transit's own departures recounted under the
  peer's published rule (its window early and late). The peer's figure and
  ours then use the same window. Differences in what moment is timed and at
  which stops remain, and are stated.
- B, converted: the peer's published share turned into a share under our
  window (1 minute early to 5 minutes late). Nobody knows the peer's
  deviations, so we assume they look like ours, stretched or squeezed by a
  factor from ``SCALES`` and shifted until they reproduce the peer's figure
  under its own rule; each assumption gives one converted share, and we
  report the lowest and highest. It is a range, never a single number.

- A, our method on their buses: since ``compare/0.2.0``, the latest
  two-hour sample of the peer's live feed that ``pipeline/compare_sample.py``
  ran through our own method (``compare/<agency_id>/<date>.json``, written
  by the Compare workflow from ``reference/peers/feeds.csv``): the share on
  time under our rule with its 95% interval. One sample of two hours is a
  small slice of a week of service, and is labeled so.

Writes ``compare/summary.json`` on the data branch. Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pipeline.common import DayFiles
from pipeline.stats import median, quantile, share

COMPARE_VERSION = "compare/0.2.0"  # 0.2.0: class A samples
OUR_WINDOW = (1.0, 5.0)  # minutes early, minutes late
WINDOW_DAYS = 28  # service days pooled for our deviations
SCALES = (0.4, 0.5, 0.6, 0.8, 1.0, 1.25, 1.6)
PEERS = Path(__file__).resolve().parents[1] / "reference" / "peers" / "definitions.csv"
FEEDS = PEERS.with_name("feeds.csv")
SAMPLE_KEYS = (
    "service_date",
    "sample_start",
    "sample_end",
    "timepoints",
    "trips_measured",
    "moments_measured",
    "on_time_share",
    "on_time_interval_95",
    "by_stop_radius_m",
)


def our_deviations(data: Path) -> tuple[list[int], dict[str, Any]]:
    """Every deviation (seconds) at timepoints over the last 28 service days,
    and where they come from."""
    days = [d for d in DayFiles(data / "ontime" / "days").all() if d.get("service_expected", True)]
    window = days[-WINDOW_DAYS:]
    devs = [x for d in window for stops in d.get("by_route_stop", {}).values()
            for b in stops.values() for x in b.get("deviations_s", [])]  # fmt: skip
    return devs, {
        "first_service_date": window[0]["service_date"] if window else None,
        "last_service_date": window[-1]["service_date"] if window else None,
        "service_days": len(window),
        "departures": len(devs),
    }


def share_within(devs: Sequence[float], early_min: float, late_min: float) -> float | None:
    """Share of deviations from ``early_min`` minutes early to ``late_min``
    minutes late, inclusive."""
    lo, hi = -60 * early_min, 60 * late_min
    return share(sum(lo <= x <= hi for x in devs), len(devs), 4)


def _shift_to(
    devs: Sequence[float], scale: float, window: tuple[float, float], target: float
) -> float | None:
    """The shift (seconds) that, applied after ``scale``, gives ``target``
    under ``window``; the later of the two when there are two (buses run
    late more often than early). None if no shift reaches it."""
    lo, hi = -60 * window[0], 60 * window[1]
    xs = sorted(scale * x for x in devs)
    if not xs:
        return None

    def at(delta: float) -> float:
        return sum(lo <= x + delta <= hi for x in xs) / len(xs)

    # The share is highest at some shift and falls on either side of it.
    # Find that shift by scanning (to the nearest 10 seconds), then search
    # the later side, where the share falls as the shift grows.
    grid = [10.0 * k for k in range(-720, 721)]
    peak = max(grid, key=at)
    if at(peak) < target:
        return None
    a, b = peak, peak + 4 * 3600.0
    for _ in range(60):
        m = (a + b) / 2
        if at(m) >= target:
            a = m
        else:
            b = m
    return a


def converted_range(
    devs: Sequence[float], window: tuple[float, float], published: float
) -> dict[str, Any]:
    """Class B: the peer's share under our window, one value per assumed
    shape of its deviations, with the lowest and highest."""
    values = []
    for s in SCALES:
        delta = _shift_to(devs, s, window, published)
        if delta is None:
            continue
        values.append(share_within([s * x + delta for x in devs], *OUR_WINDOW))
    vals = [v for v in values if v is not None]
    return {
        "low": min(vals) if vals else None,
        "high": max(vals) if vals else None,
        "assumptions_used": len(vals),
        "assumptions_tried": len(SCALES),
    }


def _num(value: str) -> float | None:
    try:
        return float(value) if value not in ("", None) else None
    except ValueError:
        return None


def read_peers(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def latest_samples(data: Path, feeds: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Agency name -> its latest class A sample with a measured share."""
    out = {}
    for f in feeds:
        folder = data / "compare" / f["agency_id"]
        for path in sorted(folder.glob("*.json"), reverse=True) if folder.is_dir() else []:
            s = json.loads(path.read_text(encoding="utf-8"))
            if s.get("on_time_share") is not None:
                out[f["agency"]] = {k: s.get(k) for k in SAMPLE_KEYS}
                break
    return out


def summarize(
    devs: list[int],
    about: dict[str, Any],
    peers: list[dict[str, str]],
    samples: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ours = share_within(devs, *OUR_WINDOW)
    rows = []
    for p in peers:
        early, late = _num(p.get("window_early_min", "")), _num(p.get("window_late_min", ""))
        published = _num(p.get("published_share", ""))
        row: dict[str, Any] = {
            "agency": p["agency"],
            "region": p.get("region", ""),
            "country": p.get("country", ""),
            "window": [early, late] if early is not None and late is not None else None,
            "measured": " ".join(
                x for x in (p.get("measured_moment", ""), p.get("measured_where", "")) if x
            ),
            "cancellations": p.get("cancellations", ""),
            "published_share": published,
            "published_period": p.get("published_period", ""),
            "note": p.get("metric_note", ""),
            "sources": (p.get("source_urls") or "").split(),
            "confidence": p.get("confidence", ""),
            "classes": [],
        }
        if early is not None and late is not None and devs:
            row["island_under_their_rule"] = share_within(devs, early, late)
            row["classes"].append("R")
            if published is not None:
                row["converted_to_our_window"] = converted_range(devs, (early, late), published)
                if row["converted_to_our_window"]["low"] is not None:
                    row["classes"].append("B")
        sample = (samples or {}).get(p["agency"])
        if sample is not None:
            row["our_method_on_their_feed"] = sample
            row["classes"].append("A")
        rows.append(row)
    return {
        "compare_version": COMPARE_VERSION,
        "our_window": list(OUR_WINDOW),
        "island_transit": {
            **about,
            # Every moment counted alike, so it compares with the recounts
            # under each peer's rule. Our published figure also counts an
            # early arrival at a last stop as on time, and is a little higher.
            "recount_under_our_rule": ours,
            "deviation_s_p25": quantile(devs, 0.25),
            "deviation_s_median": median(devs),
            "deviation_s_p75": quantile(devs, 0.75),
        },
        "assumed_scales": list(SCALES),
        "peers": rows,
    }


def write(data: Path, peers_path: Path = PEERS, feeds_path: Path = FEEDS) -> dict[str, Any] | None:
    devs, about = our_deviations(data)
    if not devs:
        return None
    samples = latest_samples(data, read_peers(feeds_path))
    out = summarize(devs, about, read_peers(peers_path), samples)
    (data / "compare").mkdir(parents=True, exist_ok=True)
    (data / "compare" / "summary.json").write_text(
        json.dumps(out, indent=1) + "\n", encoding="utf-8"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compare on-time figures with other bus systems.")
    ap.add_argument("--data", type=Path, default=Path("data-branch"))
    ap.add_argument("--peers", type=Path, default=PEERS)
    args = ap.parse_args(argv)
    out = write(args.data, args.peers)
    if out is None:
        print("compare: no deviations yet")
        return 0
    ours = out["island_transit"]["recount_under_our_rule"]
    print(f"compare: {len(out['peers'])} peers; ours {ours}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
