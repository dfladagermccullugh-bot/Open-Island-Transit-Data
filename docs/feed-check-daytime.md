# Feed check (islandtransit), 2026-11-19 07:31 PST

12 polls, 20 s apart. Produced by `scripts/verify_feed.py`; nothing here is edited by hand.

## 1. Fetches

| feed | requests | HTTP 200 + decoded | entities (min–max) | bytes (min–max) | median ms | content changed between polls |
|---|---|---|---|---|---|---|
| vehiclePositions | 12 | 12 | 18–20 | 1428–1586 | 428 | 11 of 11 |
| tripUpdates | 12 | 12 | 29–31 | 14607–16649 | 444 | 4 of 11 |
| serviceAlerts | 2 | 2 | 0–0 | 15–15 | 422 | 0 of 1 |

## 2. Freshness and update rate

- Header timestamp age at receipt: median 0.4 s, max 1.0 s.
- Vehicle timestamp age at receipt: median 5 s, 95th percentile 10 s, max 86 s.
- Vehicles seen: 20. Distinct timestamps per vehicle over 12 polls: median 12, min 1, max 12.
- Gap between consecutive distinct vehicle timestamps: median 20 s, max 30 s.

## 3. Fields populated (last successful poll)

**Vehicle positions**: 20 rows

| field | populated |
|---|---|
| entity_id | 100% |
| vehicle_id | 100% |
| vehicle_label | 100% |
| vehicle_ts | 100% |
| trip_id | 100% |
| route_id | 100% |
| direction_id | 0% |
| start_date | 0% |
| start_time | 0% |
| trip_schedule_relationship | 0% |
| lat | 100% |
| lon | 100% |
| bearing | 100% |
| speed | 100% |
| odometer | 0% |
| current_stop_sequence | 100% |
| stop_id | 100% |
| current_status | 0% |
| congestion_level | 0% |
| occupancy_status | 100% |
| occupancy_percentage | 0% |

**Trip updates (per stop)**: 544 rows

| field | populated |
|---|---|
| entity_id | 100% |
| trip_id | 100% |
| route_id | 100% |
| direction_id | 0% |
| start_date | 0% |
| start_time | 0% |
| trip_schedule_relationship | 0% |
| vehicle_id | 100% |
| trip_update_ts | 100% |
| trip_delay | 0% |
| stop_sequence | 100% |
| stop_id | 100% |
| arrival_time | 100% |
| arrival_delay | 0% |
| arrival_uncertainty | 0% |
| departure_time | 100% |
| departure_delay | 0% |
| departure_uncertainty | 0% |
| stop_schedule_relationship | 0% |

**Alerts**: 0 rows

_none_

Occupancy values seen: FEW_SEATS_AVAILABLE, MANY_SEATS_AVAILABLE

## 4. Match against static GTFS (Passio)

| feed | id | distinct in feed | found in static | match | examples not found |
|---|---|---|---|---|---|
| vehiclePositions | trip_id | 20 | 20 | 100% |  |
| vehiclePositions | route_id | 13 | 13 | 100% |  |
| vehiclePositions | stop_id | 20 | 20 | 100% |  |
| tripUpdates | trip_id | 30 | 30 | 100% |  |
| tripUpdates | route_id | 13 | 13 | 100% |  |
| tripUpdates | stop_id | 329 | 329 | 100% |  |

## 5. Do tripUpdates carry real predictions?

- Stop updates: 544; with `delay`: 0; with `time`: 544.
- Predicted minus scheduled arrival, 544 stop updates: median 136 s, 10th–90th percentile -1489 to 1824 s.
- Exactly equal to schedule: 1 (0%); whole-minute differences: 10 (2%).
- Reading: predictions differ from the schedule by plausible amounts, so they look real.

## Verdict

**Usable.** 20 of 20 vehicles in the last poll carried a trip ID, a position and a timestamp.

