"""Watchdog: is the collector still collecting?

Exits 1 and prints the reasons if anything is wrong, so the scheduled
workflow fails (GitHub emails the repository owner) and an issue is opened.

Checks:
  - each real-time feed has a successful fetch recently
  - during service hours the positions feed is not empty
  - the collector is not backing off
  - the database is not filling up
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.archive.store import Collector  # noqa: E402
from pipeline.config import PACIFIC  # noqa: E402

MAX_AGE_S = {"vehiclePositions": 600, "tripUpdates": 600, "serviceAlerts": 1800}
DATABASE_WARN_BYTES = 300 * 1024 * 1024  # free tier allows 500 MB
OLDEST_RAW_WARN = timedelta(days=10)


def in_service_hours(now: datetime) -> bool:
    """Hours when buses are certainly scheduled on every day of the week."""
    local = now.astimezone(PACIFIC)
    return 8 <= local.hour < 17


def problems(collector: Collector, now: datetime) -> list[str]:
    out: list[str] = []
    status = {row["feed"]: row for row in collector.status()}
    for feed, limit in MAX_AGE_S.items():
        row = status.get(feed)
        age = row and row["last_ok_age_s"]
        if age is None:
            out.append(f"{feed}: no successful fetch in the last 2 days")
        elif age > limit:
            out.append(f"{feed}: last successful fetch was {age // 60} minutes ago")

    vp = status.get("vehiclePositions")
    if (
        vp
        and vp["last_ok_age_s"] is not None
        and vp["last_ok_age_s"] <= MAX_AGE_S["vehiclePositions"]
        and in_service_hours(now)
        and vp["last_entity_count"] == 0
    ):
        out.append("vehiclePositions: feed is empty during service hours")

    health = collector.health()
    if health["backoff_until"]:
        out.append(f"collector is backing off after {health['consecutive_failures']} failing ticks")
    if health["database_bytes"] > DATABASE_WARN_BYTES:
        out.append(f"database is {health['database_bytes'] / 1e6:.0f} MB (limit 500 MB)")
    if health["oldest_raw_at"]:
        oldest = datetime.fromisoformat(health["oldest_raw_at"])
        if now - oldest > OLDEST_RAW_WARN:
            out.append(
                f"oldest unarchived response is from {oldest:%Y-%m-%d}: "
                "the nightly archive or the prune job is not keeping up"
            )
    return out


def main() -> int:
    now = datetime.now(UTC)
    try:
        found = problems(Collector(), now)
    except Exception as exc:
        found = [f"could not read collector status: {exc}"]
    if found:
        print("Collector problems at " + now.isoformat(timespec="seconds"))
        for p in found:
            print(f"- {p}")
        return 1
    print("collector OK at " + now.isoformat(timespec="seconds"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
