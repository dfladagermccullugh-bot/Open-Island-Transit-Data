"""Randomness of gaps: are unobserved trips spread at random?

docs/methods.md, section 2 (Nonresponse). Reads the per-trip coverage rows
in every ``quality/days/*.json`` file and writes ``quality/gaps.json``:

  rates   share of scheduled trips with no position report, by hour, hour
          band, route, day type and number of trips in service
  model   logistic regression of "trip not observed" on hour band, route,
          day type and number of scheduled trips in service at the trip's
          start; fitted only once there are MIN_DAYS days and MIN_UNOBSERVED
          unobserved trips

The model uses Firth's penalised likelihood, which gives finite estimates
when a route has no unobserved trips at all (ordinary maximum likelihood
does not). The joint likelihood-ratio test of all predictors asks whether
gaps depend on when, where and how busy service is; a small p-value means
they are not missing completely at random.

Limits: the model treats every trip as independent. Trips on the same day or
run by the same bus tend to be lost together (one bus with a failed tracker
loses a whole block of trips), so the standard errors are too small and the
p-values too confident. The rates do not have this problem. Days on which
Island Transit runs no service are left out (``pipeline/gtfs/holidays.py``).
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from pipeline.common import DayFiles

GAPS_VERSION = "gaps/0.3.0"
MIN_DAYS = 14
MIN_UNOBSERVED = 30
MIN_ROUTE_TRIPS = 30  # routes with fewer scheduled trips are pooled as "other"

HOUR_BANDS = (  # Pacific hours, [start, end)
    ("early (before 7)", 0, 7),
    ("morning peak (7-9)", 7, 9),
    ("midday (9-15)", 9, 15),
    ("afternoon peak (15-18)", 15, 18),
    ("evening (18 on)", 18, 24),
)
REFERENCE_BAND = "midday (9-15)"
BUSY_BINS = ((1, 9), (10, 14), (15, 19), (20, 99))


def day_type(d: date) -> str:
    return {5: "saturday", 6: "sunday"}.get(d.weekday(), "weekday")


def hour_band(hour: int) -> str:
    return next(name for name, lo, hi in HOUR_BANDS if lo <= hour < hi)


def collect(
    days_dir: Path, files: DayFiles | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Trip rows from every day file that has them, and the days used."""
    rows, used = [], []
    for m in (files or DayFiles(days_dir)).all():
        trips = (m.get("coverage") or {}).get("trips")
        if not trips or not (m.get("schedule") or {}).get("service_expected", True):
            continue  # no trip rows, or a day with no service: nothing to explain
        d = date.fromisoformat(m["service_date"])
        cols = trips["columns"]
        for r in trips["rows"]:
            row = dict(zip(cols, r, strict=True))
            row["service_date"] = d.isoformat()
            row["day_type"] = day_type(d)
            rows.append(row)
        used.append(d.isoformat())
    return rows, used


def _rate_table(rows: list[dict[str, Any]], key, order=None) -> dict[str, dict[str, Any]]:
    c: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        k = str(key(r))
        c[k]["scheduled"] += 1
        c[k]["unobserved"] += not r["observed"]
    rank = order.index if order else _sort_key
    return {
        k: {**v, "rate": round(v["unobserved"] / v["scheduled"], 6)}
        for k, v in sorted(c.items(), key=lambda kv: rank(kv[0]))
    }


def _sort_key(k: str) -> tuple[int, str]:
    return (int(k), "") if k.isdigit() else (0, k)


def _busy_bin(n: int) -> str:
    return next(f"{lo}-{hi}" if hi < 99 else f"{lo}+" for lo, hi in BUSY_BINS if lo <= n <= hi)


def rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "by_hour": _rate_table(rows, lambda r: r["start_hour"]),
        "by_hour_band": _rate_table(
            rows, lambda r: hour_band(r["start_hour"]), [b[0] for b in HOUR_BANDS]
        ),
        "by_route": _rate_table(rows, lambda r: r["route_id"]),
        "by_day_type": _rate_table(rows, lambda r: r["day_type"]),
        "by_trips_in_service": _rate_table(rows, lambda r: _busy_bin(r["scheduled_in_service"])),
    }


# --- model ----------------------------------------------------------------------


def firth_logit(
    X: np.ndarray, y: np.ndarray, max_iter: int = 100, tol: float = 1e-8
) -> dict[str, Any]:
    """Firth-penalised logistic regression (Firth 1993; Heinze and Schemper
    2002). Returns coefficients, standard errors from the inverse Fisher
    information, the penalised log-likelihood and whether it converged."""
    n, k = X.shape
    beta = np.zeros(k)

    def loglik(b: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
        p = 1 / (1 + np.exp(-(X @ b)))
        p = np.clip(p, 1e-12, 1 - 1e-12)
        info = X.T @ (X * (p * (1 - p))[:, None])
        sign, logdet = np.linalg.slogdet(info)
        ll = float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)) + 0.5 * logdet)
        return ll, p, info

    ll, p, info = loglik(beta)
    converged = False
    for _ in range(max_iter):
        cov = np.linalg.pinv(info)
        w = p * (1 - p)
        # Hat-matrix diagonal of W^1/2 X (X'WX)^-1 X' W^1/2.
        h = np.einsum("ij,jk,ik->i", X, cov, X) * w
        score = X.T @ (y - p + h * (0.5 - p))
        step = cov @ score
        # Halve the step until the penalised likelihood does not fall.
        for _ in range(30):
            new_ll, new_p, new_info = loglik(beta + step)
            if new_ll >= ll - 1e-10:
                break
            step = step / 2
        beta = beta + step
        ll, p, info = new_ll, new_p, new_info
        if np.max(np.abs(step)) < tol:
            converged = True
            break
    se = np.sqrt(np.clip(np.diag(np.linalg.pinv(info)), 0, None))
    return {"beta": beta, "se": se, "loglik": ll, "converged": converged, "n": n}


def _p_normal(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def _p_chi2(stat: float, df: int) -> float:
    """Upper tail of the chi-square distribution (regularised gamma)."""
    if stat <= 0:
        return 1.0
    a, x = df / 2, stat / 2
    if x < a + 1:  # series
        term = total = 1 / a
        for n in range(1, 500):
            term *= x / (a + n)
            total += term
            if term < total * 1e-14:
                break
        return max(0.0, 1 - total * math.exp(-x + a * math.log(x) - math.lgamma(a)))
    # Continued fraction (Lentz).
    tiny = 1e-300
    b, c, d = x + 1 - a, 1 / tiny, 1 / (x + 1 - a)
    f = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        f *= d * c
        if abs(d * c - 1) < 1e-14:
            break
    return min(1.0, math.exp(-x + a * math.log(x) - math.lgamma(a)) * f)


def design(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, list[str], dict]:
    """Design matrix with an intercept and treatment-coded factors. Each
    factor's reference level is named in ``refs``; levels absent from the
    data get no column."""
    route_n = Counter(r["route_id"] for r in rows)
    route_of = {k: (k if n >= MIN_ROUTE_TRIPS else "other") for k, n in route_n.items()}
    routes = Counter(route_of[r["route_id"]] for r in rows)
    ref_route = max(routes, key=lambda k: (routes[k], k != "other", k))
    bands = Counter(hour_band(r["start_hour"]) for r in rows)
    ref_band = REFERENCE_BAND if REFERENCE_BAND in bands else max(bands, key=bands.get)
    dtypes = Counter(r["day_type"] for r in rows)
    ref_dtype = "weekday" if "weekday" in dtypes else max(dtypes, key=dtypes.get)
    busy = np.array([r["scheduled_in_service"] for r in rows], dtype=float)
    busy_mean = float(busy.mean())

    names = ["intercept"]
    cols = [np.ones(len(rows))]
    for label, levels, ref, value in (
        ("hour band", bands, ref_band, lambda r: hour_band(r["start_hour"])),
        ("route", routes, ref_route, lambda r: route_of[r["route_id"]]),
        ("day type", dtypes, ref_dtype, lambda r: r["day_type"]),
    ):
        for level in sorted(levels):
            if level != ref:
                names.append(f"{label}: {level}")
                cols.append(np.array([value(r) == level for r in rows], dtype=float))
    if busy.std() > 0:
        names.append("trips in service (per trip, centred)")
        cols.append(busy - busy_mean)
    X = np.column_stack(cols)
    y = np.array([not r["observed"] for r in rows], dtype=float)
    refs = {
        "hour band": ref_band,
        "route": ref_route,
        "day type": ref_dtype,
        "trips in service centred at": round(busy_mean, 3),
        "routes pooled as other": sorted(k for k, v in route_of.items() if v == "other"),
    }
    return X, y, names, refs


def model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    X, y, names, refs = design(rows)
    full = firth_logit(X, y)
    null = firth_logit(X[:, :1], y)
    lr = max(0.0, 2 * (full["loglik"] - null["loglik"]))
    df = X.shape[1] - 1
    terms = []
    for name, b, se in zip(names, full["beta"], full["se"], strict=True):
        z = b / se if se > 0 else None
        terms.append({
            "term": name,
            "coef": round(float(b), 4),
            "se": round(float(se), 4),
            "odds_ratio": round(math.exp(b), 4),
            "or_95ci": [round(math.exp(b - 1.96 * se), 4), round(math.exp(b + 1.96 * se), 4)],
            "p": round(_p_normal(z), 6) if z is not None else None,
        })  # fmt: skip
    return {
        "method": "logistic regression, Firth penalised likelihood",
        "outcome": "trip had no position report carrying its trip_id",
        "trips": int(len(y)),
        "unobserved": int(y.sum()),
        "converged": bool(full["converged"] and null["converged"]),
        "reference_levels": refs,
        "terms": terms,
        "lr_test_all_predictors": {
            "statistic": round(lr, 4),
            "df": df,
            "p": round(_p_chi2(lr, df), 6) if df else None,
        },
    }


def summarise(days_dir: Path, files: DayFiles | None = None) -> dict[str, Any]:
    every, used = collect(days_dir, files)
    # A trip the feed said was canceled, and that no bus reported, is not a
    # gap in the data: there was nothing to report.
    rows = [r for r in every if r["observed"] or not r.get("canceled", False)]
    unobserved = sum(not r["observed"] for r in rows)
    out: dict[str, Any] = {
        "gaps_version": GAPS_VERSION,
        "days": used,
        "trips": len(rows),
        "unobserved": unobserved,
        "canceled_left_out": len(every) - len(rows),
        "thresholds": {"min_days": MIN_DAYS, "min_unobserved": MIN_UNOBSERVED},
        "rates": rates(rows) if rows else None,
        "model": None,
        "model_status": None,
        "limits": "The model treats trips as independent. Trips lost together on one day or "
        "one bus make its standard errors too small and its p-values too confident. Days with "
        "no service are left out, and so are trips the feed marked as canceled that no bus "
        "reported.",
    }
    if len(used) < MIN_DAYS or unobserved < MIN_UNOBSERVED:
        out["model_status"] = (
            f"not fitted: needs {MIN_DAYS} days and {MIN_UNOBSERVED} unobserved trips "
            f"(have {len(used)} and {unobserved})"
        )
    else:
        out["model"] = model(rows)
        out["model_status"] = "fitted" if out["model"]["converged"] else "fitted, not converged"
    return out


def write(out: Path, files: DayFiles | None = None) -> dict[str, Any]:
    g = summarise(out / "days", files)
    (out / "gaps.json").write_text(json.dumps(g, indent=1) + "\n")
    return g
