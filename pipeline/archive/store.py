"""Read the collector's public tables over its REST interface."""

from __future__ import annotations

import base64
import gzip
import hashlib
import time
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import requests

from pipeline.archive.days import day_bounds, pacific_date
from pipeline.common import Row
from pipeline.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL, USER_AGENT

RETRY_WAITS_S = (1, 4, 16)


class Collector:
    def __init__(self, url: str = SUPABASE_URL, key: str = SUPABASE_PUBLISHABLE_KEY) -> None:
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"apikey": key, "User-Agent": USER_AGENT})

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """One REST call, retried on a transport error or a 5xx/429 reply
        after 1, 4 and 16 seconds. Both calls used here only read, so a
        repeat is safe."""
        last: Exception | None = None
        for attempt in range(len(RETRY_WAITS_S) + 1):
            if attempt:
                time.sleep(RETRY_WAITS_S[attempt - 1])
            try:
                r = self.session.request(
                    method, f"{self.url}/rest/v1/{path}", timeout=120, **kwargs
                )
                if r.status_code < 500 and r.status_code != 429:
                    r.raise_for_status()  # a 4xx will not get better: fail now
                    return r.json()
                last = requests.HTTPError(f"HTTP {r.status_code}", response=r)
            except requests.HTTPError:
                raise
            except requests.RequestException as exc:
                last = exc
        raise RuntimeError(f"{method} {path} failed after retries: {last}")

    def _get(self, path: str, params: dict[str, str] | list[tuple[str, str]]) -> Any:
        return self._request("GET", path, params=params)

    def _rpc(self, name: str, args: dict[str, Any]) -> Any:
        return self._request("POST", f"rpc/{name}", json=args)

    def _pages(self, table: str, day: date, key: str, page: int) -> Iterator[Row]:
        start, end = day_bounds(day)
        last = 0
        while True:
            rows = self._get(
                table,
                [
                    ("select", "*"),
                    ("logged_at", f"gte.{start.isoformat()}"),
                    ("logged_at", f"lt.{end.isoformat()}"),
                    (key, f"gt.{last}"),
                    ("order", f"{key}.asc"),
                    ("limit", str(page)),
                ],
            )
            yield from rows
            if len(rows) < page:
                return
            last = rows[-1][key]

    def fetch_log(self, day: date) -> list[Row]:
        return list(self._pages("fetch_log", day, "fetch_id", 1000))

    def raw(self, day: date) -> Iterator[Row]:
        """Rows that have a stored body, with ``body`` decoded to the exact
        bytes received. Raises if a body does not match its logged hash."""
        for row in self._pages("raw_export", day, "fetch_id", 200):
            body = gzip.decompress(base64.b64decode(row.pop("body_gz_b64")))
            if hashlib.sha256(body).hexdigest() != row["sha256"] or len(body) != row["bytes"]:
                raise ValueError(f"fetch {row['fetch_id']}: stored body does not match its hash")
            row["body"] = body
            yield row

    def static(self, day: date) -> list[Row]:
        rows = []
        for row in self._pages("static_export", day, "static_fetch_id", 5):
            b64 = row.pop("body_b64")
            row["body"] = base64.b64decode(b64) if b64 else None
            if row["body"] is not None and hashlib.sha256(row["body"]).hexdigest() != row["sha256"]:
                raise ValueError(f"static fetch {row['static_fetch_id']}: body does not match hash")
            rows.append(row)
        return rows

    def day_digest(self, day: date) -> Row:
        return self._rpc("day_digest", {"p_date": day.isoformat()})[0]

    def status(self) -> list[Row]:
        return self._get("status_now", {"select": "*"})

    def health(self) -> Row:
        return self._rpc("collector_health", {})[0]

    def recent_fetches(self, feed: str, limit: int) -> list[Row]:
        """The latest ``limit`` log rows of one feed, newest first."""
        return self._get(
            "fetch_log",
            {
                "select": "requested_at,http_status,error_class,entity_count,newest_entity_ts",
                "feed": f"eq.{feed}",
                "order": "fetch_id.desc",
                "limit": str(limit),
            },
        )

    def recent_static(self, limit: int = 6) -> list[Row]:
        """The latest static GTFS downloads, newest first, without the zips."""
        return self._get(
            "static_gtfs_fetch",
            {
                "select": "source,logged_at,http_status,error_class,zip_has_directory",
                "order": "static_fetch_id.desc",
                "limit": str(limit),
            },
        )

    def recent_prunes(self, limit: int = 20) -> list[Row]:
        """The latest rows of the prune log, newest first."""
        return self._get(
            "prune_log", {"select": "*", "order": "prune_id.desc", "limit": str(limit)}
        )

    def first_day(self) -> date | None:
        rows = self._get(
            "fetch_log", {"select": "logged_at", "order": "fetch_id.asc", "limit": "1"}
        )
        if not rows:
            return None
        return pacific_date(datetime.fromisoformat(rows[0]["logged_at"]))
