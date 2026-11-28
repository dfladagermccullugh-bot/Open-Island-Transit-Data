"""The columns of the two daily tables, and where each value comes from.

``ontime/daily.csv`` takes its columns from the top level of each on-time
day file, and ``quality/daily.csv`` takes each of its columns from a path in
the quality day file. They are written down here, apart from the stages that
write the tables, so that the publishing gate (``pipeline/invariants.py``)
and the data dictionary can read them without loading numpy or pyarrow.
"""

from __future__ import annotations

ONTIME_DAILY = [
    "service_date",
    "scheduled",
    "observed",
    "observed_share",
    "on_time",
    "early",
    "late",
    "on_time_share_of_observed",
    "deviation_s_median",
    "feed_agreement_share",
    "service_expected",
    "ontime_version",
]

QUALITY_HEADLINE: list[tuple[str, tuple[str, ...]]] = [
    ("service_date", ("service_date",)),
    ("service_expected", ("schedule", "service_expected")),
    ("passio_version", ("schedule", "passio_version")),
    ("scheduled_trips", ("coverage", "scheduled_trips")),
    ("trip_coverage", ("coverage", "trip_coverage")),
    ("vehicle_uptime", ("paradata", "vehiclePositions", "uptime")),
    ("trip_update_uptime", ("paradata", "tripUpdates", "uptime")),
    ("vehicle_fetch_failure_rate", ("nonresponse", "fetch", "vehiclePositions", "rate")),
    ("empty_in_service_rate", ("nonresponse", "vehicle_feed", "empty_in_service_rate")),
    ("stale_in_service_rate", ("nonresponse", "vehicle_feed", "stale_in_service_rate")),
    ("longest_gap_s_median", ("nonresponse", "trip_gaps", "longest_gap_s_median")),
    ("position_age_s_median", ("measurement", "position_age_s_median")),
    ("position_age_s_p95", ("measurement", "position_age_s_p95")),
    ("impossible_jumps", ("measurement", "impossible_jumps_over_130_kmh")),
    ("timepoint_coverage", ("events", "stop_coverage", "timepoints", "coverage")),
    ("stop_coverage", ("events", "stop_coverage", "all_stops", "coverage")),
    ("off_shape_share", ("events", "shape_fit", "off_shape_share")),
    ("prediction_mae_s_5min", ("events", "prediction_error", "by_horizon_min", "5", "mae_s")),
    (
        "prediction_median_abs_s_5min",
        ("events", "prediction_error", "by_horizon_min", "5", "median_abs_s"),
    ),
    (
        "prediction_over_30min_share_5min",
        ("events", "prediction_error", "by_horizon_min", "5", "over_30min_share"),
    ),
    (
        "prediction_within_60s_5min",
        ("events", "prediction_error", "by_horizon_min", "5", "within_60s_share"),
    ),
    ("jitter_m_p95", ("events", "jitter", "jitter_m_p95")),
    ("unassignable_trip_ids", ("events", "assignment", "unassignable")),
    ("vehicles_missing_vs_30d", ("events", "missing_vehicles", "missing_count")),
    ("trip_id_match_rate", ("processing", "id_match", "trip_id", "match_rate")),
    ("over_coverage_share", ("coverage", "over_coverage", "share")),
    ("vehicle_latency_ms_median", ("paradata", "vehiclePositions", "latency_ms_median")),
    ("vehicles_seen", ("coverage", "vehicles_seen")),
    ("timetable_reference", ("processing", "timepoints", "reference")),
    ("timepoint_names_resolved_share", ("processing", "timepoints", "resolved_share")),
    ("canceled_trips", ("coverage", "canceled_trips")),
    ("trip_coverage_excluding_canceled", ("coverage", "trip_coverage_excluding_canceled")),
    ("trip_update_changed_share", ("paradata", "tripUpdates", "refresh", "changed_share")),
    (
        "trip_update_interval_s_median",
        ("paradata", "tripUpdates", "refresh", "update_interval_s_median"),
    ),
    ("passio_basis", ("schedule", "passio_basis")),
    ("passio_changed_during_day", ("schedule", "passio_changed_during_day")),
    ("glitch_reports_removed", ("measurement", "glitches", "removed_reports")),
    ("unresolved_jumps", ("measurement", "glitches", "unresolved_jumps")),
    ("complete", ("complete",)),
    # Since quality/0.8.0.
    *(
        (
            f"{pos}_stop_timepoint_coverage",
            ("events", "stop_coverage", "timepoints", "by_position", pos, "coverage"),
        )
        for pos in ("first", "middle", "last")
    ),
    ("between_trips_share", ("events", "between_trips", "between_trips_share")),
    ("between_trips_unassigned_share", ("events", "between_trips", "unassigned_share")),
    ("quality_version", ("quality_version",)),
]
