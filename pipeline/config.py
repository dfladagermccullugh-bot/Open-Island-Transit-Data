"""Public configuration. Nothing here is secret.

The publishable key only allows reading the collector's public tables and
views; see docs/collector-setup.md.
"""

from __future__ import annotations

import os
from zoneinfo import ZoneInfo

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ekczqlmunyumtgtthppb.supabase.co")
SUPABASE_PUBLISHABLE_KEY = os.environ.get(
    "SUPABASE_PUBLISHABLE_KEY", "sb_publishable_AWlvBYVrlTz7dEaf7RwwzA_5zRN3af-"
)

REPO = "dfladagermccullugh-bot/Open-Island-Transit-Data"
PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = f"OpenIslandTransitData/0.1 (+https://github.com/{REPO})"
