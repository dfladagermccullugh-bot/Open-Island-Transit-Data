"""Read the collector's public tables over its REST interface."""

from __future__ import annotations

import base64
import gzip
import hashlib
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

import requests

from pipeline.archive.days import day_bounds, pacific_date
from pipeline.config import SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL, USER_AGENT

Row = dict[str, Any]


class Collector:
    def __init__(self, url: str = SUPABASE_URL, key: str = SUPABASE_PUBLISHABLE_KEY) -> None:
        self.url = url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"apikey": key, "User-Agent": USER_AGENT})

    def _get(self, path: str, params: dict[str, str] | list[tuple[str, str]]) -> Any:
        last: Exception | None = None
        for _attempt in range(4):
            try:
                r = self.session.get(f"{self.url}/rest/v1/{path}", params=params, timeout=120)
                r.raise_for_status()
                return r.json()
            except requests.RequestException as exc:  # retry transient failures
                last = exc
        raise RuntimeError(f"GET {path} failed after retries: {last}")

    def _rpc(self, name: str, args: dict[str, Any]) -> Any:
        r = self.session.post(f"{self.url}/rest/v1/rpc/{name}", json=args, timeout=120)
        r.raise_for_status()
        return r.json()

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

    def first_day(self) -> date | None:
        rows = self._get(
            "fetch_log", {"select": "logged_at", "order": "fetch_id.asc", "limit": "1"}
        )
        if not rows:
            return None
        return pacific_date(datetime.fromisoformat(rows[0]["logged_at"]))
