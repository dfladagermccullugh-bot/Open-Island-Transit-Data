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

- A, our method on their buses: since ``compare/0.2.0``, samples of the
  peer's live feed that ``pipeline/compare_sample.py`` ran through our own
  method (``compare/<agency_id>/<date>.json``, written by the Compare
  workflow from ``reference/peers/feeds.csv``): the share on time under our
  rule with its 95% interval. Since ``compare/0.3.0`` every sample of the
  last ``POOL_DAYS`` days is pooled (``pooled_samples``), with its trips
  resampled together for the interval, and each sample is listed. Samples
  taken on or next to a US federal holiday (``reference/peers/
  no-service-days.csv``) are left out of the pool and listed with the
  reason. Island Transit's figure beside it is recounted over the same
  weekdays and clock hours (``same_hours``), from the deviations the
  on-time day files keep per hour.

Since ``compare/0.3.0`` the summary also explains the one difference between
our published figure and the recount under our own rule: the published
figure counts an arrival at a trip's last stop more than a minute early as
on time, the recount (like most agencies) as early. ``island_transit``
holds both figures and the count of such arrivals over the same 28 service
days, and gate rule I13 checks that the counts differ by exactly that.

Writes ``compare/summary.json`` on the data branch. Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipeline.common import DayFiles
from pipeline.gtfs import holidays
from pipeline.stats import median, quantile, share

COMPARE_VERSION = "compare/0.3.0"  # 0.3.0: pooled samples, same hours, holidays, one rule
OUR_WINDOW = (1.0, 5.0)  # minutes early, minutes late
WINDOW_DAYS = 28  # service days pooled for our deviations
POOL_DAYS = 56  # samples of the last 8 weeks are pooled
SCALES = (0.4, 0.5, 0.6, 0.8, 1.0, 1.25, 1.6)
RESAMPLES = 2000
SEED = 20261128  # fixed, so the pooled interval is the same each time
PEERS = Path(__file__).resolve().parents[1] / "reference" / "peers" / "definitions.csv"
FEEDS = PEERS.with_name("feeds.csv")
PEER_DAYS = PEERS.with_name("no-service-days.csv")
SAMPLE_KEYS = (
    "service_date",
    "local_weekday",
    "local_hours",
    "sample_start",
    "sample_end",
    "timepoints",
    "trips_measured",
    "moments_measured",
    "moments_on_time",
    "on_time_share",
    "on_time_interval_95",
    "trips_reported",
    "trips_in_schedule",
    "by_stop_radius_m",
)


def service_days(data: Path) -> list[dict[str, Any]]:
    return [d for d in DayFiles(data / "ontime" / "days").all() if d.get("service_expected", True)]


def our_deviations(data: Path) -> tuple[list[int], dict[str, Any]]:
    """Every deviation (seconds) at timepoints over the last 28 service days,
    and where they come from, with the counts that explain the difference
    between our published figure and the recount under our own rule."""
    window = service_days(data)[-WINDOW_DAYS:]
    devs = [x for d in window for stops in d.get("by_route_stop", {}).values()
            for b in stops.values() for x in b.get("deviations_s", [])]  # fmt: skip
    observed = sum(d.get("observed", 0) for d in window)
    on_time = sum(d.get("on_time", 0) for d in window)
    early = [d.get("last_stop_early_arrivals") for d in window]
    early_last = sum(early) if window and None not in early else None  # type: ignore[arg-type]
    recount = on_time - early_last if early_last is not None else None
    return devs, {
        "first_service_date": window[0]["service_date"] if window else None,
        "last_service_date": window[-1]["service_date"] if window else None,
        "service_days": len(window),
        "departures": len(devs),
        "observed": observed,
        "on_time": on_time,
        # The home page's figure: an early arrival at a last stop is on time.
        "headline_on_time_share": share(on_time, observed, 4),
        "last_stop_early_arrivals": early_last,
        "recount_on_time": recount,
    }


def skip_reason(day: str, path: Path = PEER_DAYS) -> str | None:
    """Why a peer sample of ``day`` is left out of the pool: the day is a US
    federal holiday or next to one. None if it is kept."""
    d = date.fromisoformat(day)
    for offset, words in ((0, "{}"), (-1, "the day after {}"), (1, "the day before {}")):
        row = holidays.no_service(d + timedelta(days=offset), path)
        if row:
            return words.format(row["holiday"])
    return None


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


def local_hours(start: datetime, end: datetime, zone: ZoneInfo) -> list[int]:
    """The clock hours in ``zone`` that the time from ``start`` to ``end``
    touches, in order."""
    a, b = start.astimezone(zone), end.astimezone(zone)
    hours, h = [], a.replace(minute=0, second=0, microsecond=0)
    while h < b:
        hours.append(h.hour)
        h = (h + timedelta(hours=1)).astimezone(zone)
    return hours


def _sample_row(s: dict[str, Any]) -> dict[str, Any]:
    """One sample as listed in the summary. Samples older than
    ``compare-sample/0.3.0`` get their local weekday and hours from their
    start, end and time zone, and their on-time count from their share."""
    row = {k: s.get(k) for k in SAMPLE_KEYS}
    zone = ZoneInfo(s.get("agency_timezone") or "America/Los_Angeles")
    start = datetime.fromisoformat(s["sample_start"]).astimezone(zone)
    end = datetime.fromisoformat(s["sample_end"]).astimezone(zone)
    if row["local_weekday"] is None:
        row["local_weekday"] = start.strftime("%A")
    if row["local_hours"] is None:
        row["local_hours"] = local_hours(start, end, zone)
    if row["moments_on_time"] is None and row["on_time_share"] is not None:
        row["moments_on_time"] = round(row["on_time_share"] * (row["moments_measured"] or 0))
    return row


def pooled_interval(trips: list[list[int]], seed: int = SEED) -> list[float | None]:
    """95% interval of the pooled share, resampling whole trips of every
    pooled sample together. ``trips``: ``[moments, on time]`` per trip."""
    if len(trips) < 5:
        return [None, None]
    rng = random.Random(seed)
    shares = []
    for _ in range(RESAMPLES):
        pick = [rng.choice(trips) for _ in trips]
        n = sum(t[0] for t in pick)
        if n:
            shares.append(sum(t[1] for t in pick) / n)
    lo, hi = quantile(shares, 0.025), quantile(shares, 0.975)
    return [round(lo, 4) if lo is not None else None, round(hi, 4) if hi is not None else None]


def pooled_samples(data: Path, agency_id: str, path: Path = PEER_DAYS) -> dict[str, Any] | None:
    """Class A for one peer: every sample of the last ``POOL_DAYS`` days
    before its latest, pooled, except those on or next to a holiday or with
    no departure measured, which are listed with the reason. None if the
    peer has no sample at all."""
    folder = data / "compare" / agency_id
    found = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))
             ] if folder.is_dir() else []  # fmt: skip
    found = [s for s in found if s.get("service_date") and s.get("sample_start")]
    if not found:
        return None
    last = max(date.fromisoformat(s["service_date"]) for s in found)
    first = last - timedelta(days=POOL_DAYS - 1)
    kept, skipped, trips = [], [], []
    with_trips = True
    for s in sorted(found, key=lambda s: s["sample_start"]):
        if date.fromisoformat(s["service_date"]) < first:
            continue
        reason = skip_reason(s["service_date"], path)
        if reason is None and not s.get("moments_measured"):
            reason = "no departure measured"
        if reason is not None:
            skipped.append({"service_date": s["service_date"],
                            "sample_start": s["sample_start"], "reason": reason})  # fmt: skip
            continue
        kept.append(_sample_row(s))
        if isinstance(s.get("by_trip"), dict):
            trips += [list(v) for v in s["by_trip"].values()]
        else:
            with_trips = False
    moments = sum(r["moments_measured"] for r in kept)
    on_time = sum(r["moments_on_time"] for r in kept)
    reported = sum(r["trips_reported"] or 0 for r in kept)
    in_schedule = sum(r["trips_in_schedule"] or 0 for r in kept)
    slots: dict[str, set[int]] = defaultdict(set)
    for r in kept:
        slots[r["local_weekday"]].update(r["local_hours"])
    return {
        "pool_first_date": first.isoformat(),
        "pool_last_date": last.isoformat(),
        "samples_pooled": len(kept),
        "service_dates": sorted({r["service_date"] for r in kept}),
        "hours": {w: sorted(h) for w, h in sorted(slots.items())},
        "trips_measured": sum(r["trips_measured"] for r in kept),
        "moments_measured": moments,
        "moments_on_time": on_time,
        "on_time_share": share(on_time, moments, 4),
        "on_time_interval_95": pooled_interval(trips) if with_trips and kept else [None, None],
        "trips_reported": reported,
        "trips_in_schedule": in_schedule,
        # A low share means the feed and the schedule do not use the same trip ids.
        "share_in_schedule": share(in_schedule, reported, 4),
        "samples": kept,
        "samples_skipped": skipped,
    }


def same_hours(
    days: list[dict[str, Any]], hours: dict[str, list[int]], first: str, last: str,
    path: Path = PEER_DAYS,
) -> dict[str, Any]:  # fmt: skip
    """Island Transit recounted under our rule, with every moment counted
    alike, over the peer's pooled weekdays and clock hours: the service days
    from ``first`` to ``last`` on those weekdays (holidays and the days next
    to them left out, as for the peers), and only the departures scheduled in
    those hours (``by_hour``, Pacific)."""
    picked, devs = [], []
    for d in days:
        day = d["service_date"]
        weekday = date.fromisoformat(day).strftime("%A")
        if not (first <= day <= last) or weekday not in hours or skip_reason(day, path):
            continue
        picked.append(day)
        for h in hours[weekday]:
            devs += (d.get("by_hour", {}).get(str(h)) or {}).get("deviations_s", [])
    return {
        "service_dates": picked,
        "departures": len(devs),
        "recount_under_our_rule": share_within(devs, *OUR_WINDOW),
    }


def summarize(
    devs: list[int],
    about: dict[str, Any],
    peers: list[dict[str, str]],
    samples: dict[str, dict[str, Any]] | None = None,
    days: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """``samples``: agency name -> its pool (``pooled_samples``); ``days``:
    our service days, for the recount over the peers' hours."""
    # Every moment counted alike: the published count less the early
    # arrivals at last stops, or, for day files older than ontime/0.8.0,
    # the deviations counted inside the window.
    recount = about.get("recount_on_time")
    ours = (share(recount, about["observed"], 4) if recount is not None
            else share_within(devs, *OUR_WINDOW))  # fmt: skip
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
        pool = (samples or {}).get(p["agency"])
        if pool is not None:
            row["our_method_on_their_feed"] = pool
            if pool["on_time_share"] is not None:
                row["classes"].append("A")
                row["island_same_hours"] = same_hours(
                    days or [], pool["hours"], pool["pool_first_date"], pool["pool_last_date"]
                )
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
        "pool_days": POOL_DAYS,
        "peers": rows,
    }


def write(data: Path, peers_path: Path = PEERS, feeds_path: Path = FEEDS) -> dict[str, Any] | None:
    devs, about = our_deviations(data)
    if not devs:
        return None
    samples = {}
    for f in read_peers(feeds_path):
        pool = pooled_samples(data, f["agency_id"])
        if pool is not None:
            samples[f["agency"]] = pool
    out = summarize(devs, about, read_peers(peers_path), samples, service_days(data))
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
