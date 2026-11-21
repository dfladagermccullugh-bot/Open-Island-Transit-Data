import json
from datetime import date

import numpy as np
import pytest

from pipeline.quality import gaps


def test_chi2_upper_tail():
    assert gaps._p_chi2(3.841459, 1) == pytest.approx(0.05, abs=1e-6)
    assert gaps._p_chi2(10.0, 5) == pytest.approx(0.075235, abs=1e-5)
    assert gaps._p_chi2(30.0, 10) == pytest.approx(0.000857, abs=1e-5)
    assert gaps._p_chi2(0.5, 4) == pytest.approx(0.973501, abs=1e-5)


def test_firth_recovers_coefficients():
    rng = np.random.default_rng(1)
    n = 20000
    x = rng.normal(size=n)
    X = np.column_stack([np.ones(n), x])
    y = (rng.random(n) < 1 / (1 + np.exp(-(-2.0 + 0.8 * x)))).astype(float)
    fit = gaps.firth_logit(X, y)
    assert fit["converged"]
    assert fit["beta"] == pytest.approx([-2.0, 0.8], abs=0.08)


def test_firth_is_finite_under_separation():
    # Group 1 never has an event: ordinary maximum likelihood diverges.
    X = np.column_stack([np.ones(60), np.r_[np.zeros(30), np.ones(30)]])
    y = np.r_[np.ones(6), np.zeros(54)]
    fit = gaps.firth_logit(X, y)
    assert fit["converged"]
    assert np.all(np.isfinite(fit["beta"])) and -6 < fit["beta"][1] < -1


def _day_file(tmp_path, d, rows):
    f = tmp_path / f"{d}.json"
    cols = ["trip_id", "route_id", "start_hour", "scheduled_in_service", "observed"]
    f.write_text(
        json.dumps({"service_date": d, "coverage": {"trips": {"columns": cols, "rows": rows}}})
    )


def test_rates_and_model_gate(tmp_path):
    _day_file(tmp_path, "2026-11-19", [["a", "R1", 6, 2, True], ["b", "R1", 6, 3, False],
                                       ["c", "R2", 12, 1, True]])  # fmt: skip
    (tmp_path / "2026-11-18.json").write_text(json.dumps({"service_date": "2026-11-18"}))
    g = gaps.summarise(tmp_path)
    assert g["days"] == ["2026-11-19"] and g["trips"] == 3 and g["unobserved"] == 1
    assert g["rates"]["by_hour"]["6"] == {"scheduled": 2, "unobserved": 1, "rate": 0.5}
    assert g["rates"]["by_hour_band"]["early (before 7)"]["rate"] == 0.5
    assert g["rates"]["by_day_type"]["weekday"]["scheduled"] == 3
    assert g["model"] is None and g["model_status"].startswith("not fitted")


def test_model_detects_hour_dependence(tmp_path):
    rng = np.random.default_rng(7)
    start = date(2026, 11, 2).toordinal()
    for k in range(gaps.MIN_DAYS):
        d = date.fromordinal(start + k).isoformat()
        rows = []
        for i in range(80):
            hour = [5, 8, 12, 16, 19][i % 5]
            miss = rng.random() < (0.4 if hour == 5 else 0.03)
            rows.append([f"t{i}", f"R{i % 3}", hour, 1 + i % 7, not miss])
        _day_file(tmp_path, d, rows)
    g = gaps.summarise(tmp_path)
    m = g["model"]
    assert g["model_status"] == "fitted"
    assert m["reference_levels"]["hour band"] == "midday (9-15)"
    early = next(t for t in m["terms"] if t["term"] == "hour band: early (before 7)")
    assert early["odds_ratio"] > 5 and early["p"] < 1e-6
    assert m["lr_test_all_predictors"]["p"] < 1e-6
    bands = list(g["rates"]["by_hour_band"])
    assert bands == sorted(bands, key=[b[0] for b in gaps.HOUR_BANDS].index)
    assert bands[0] == "early (before 7)"
    assert {"saturday", "sunday", "weekday"} >= set(g["rates"]["by_day_type"])


def test_a_day_with_no_service_is_left_out(tmp_path):
    _day_file(tmp_path, "2026-11-25", [["a", "R1", 6, 2, True]])
    _day_file(tmp_path, "2026-11-26", [["a", "R1", 6, 2, False], ["b", "R1", 7, 2, False]])
    f = tmp_path / "2026-11-26.json"
    m = json.loads(f.read_text())
    m["schedule"] = {"service_expected": False}
    f.write_text(json.dumps(m))
    g = gaps.summarise(tmp_path)
    assert g["days"] == ["2026-11-25"] and g["unobserved"] == 0
