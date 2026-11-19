"""Turn GTFS-Realtime protobuf bytes into plain rows.

Every optional field that the feed leaves out becomes ``None``; nothing is
filled in or guessed. Which fields a feed actually populates is one of the
things this project measures (item nonresponse).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from google.transit import gtfs_realtime_pb2 as rt

Row = dict[str, Any]


@dataclass
class ParsedFeed:
    header_ts: int | None
    gtfs_realtime_version: str | None
    incrementality: str | None
    entity_count: int
    vehicles: list[Row] = field(default_factory=list)
    trip_update_stops: list[Row] = field(default_factory=list)
    trip_updates: list[Row] = field(default_factory=list)
    alerts: list[Row] = field(default_factory=list)


def _opt(msg: Any, name: str) -> Any:
    """Value of an optional scalar field, or None if the feed did not set it."""
    return getattr(msg, name) if msg.HasField(name) else None


def _enum(msg: Any, name: str) -> str | None:
    if not msg.HasField(name):
        return None
    descriptor = msg.DESCRIPTOR.fields_by_name[name].enum_type
    number = getattr(msg, name)
    value = descriptor.values_by_number.get(number)
    return value.name if value is not None else str(number)


def _text(translated: Any) -> str | None:
    """First translation of a TranslatedString (English if present)."""
    if not translated.translation:
        return None
    for t in translated.translation:
        if t.language in ("en", "en-US", ""):
            return t.text
    return translated.translation[0].text


def _trip(trip: Any) -> Row:
    return {
        "trip_id": _opt(trip, "trip_id"),
        "route_id": _opt(trip, "route_id"),
        "direction_id": _opt(trip, "direction_id"),
        "start_date": _opt(trip, "start_date"),
        "start_time": _opt(trip, "start_time"),
        "trip_schedule_relationship": _enum(trip, "schedule_relationship"),
    }


def parse_feed(data: bytes) -> ParsedFeed:
    """Parse one FeedMessage. Raises ``google.protobuf.message.DecodeError``
    if ``data`` is not a valid message."""
    msg = rt.FeedMessage()
    msg.ParseFromString(data)

    out = ParsedFeed(
        header_ts=_opt(msg.header, "timestamp"),
        gtfs_realtime_version=_opt(msg.header, "gtfs_realtime_version"),
        incrementality=_enum(msg.header, "incrementality"),
        entity_count=len(msg.entity),
    )

    for entity in msg.entity:
        if entity.HasField("vehicle"):
            v = entity.vehicle
            pos = v.position
            out.vehicles.append(
                {
                    "entity_id": entity.id,
                    "vehicle_id": _opt(v.vehicle, "id"),
                    "vehicle_label": _opt(v.vehicle, "label"),
                    "vehicle_ts": _opt(v, "timestamp"),
                    **_trip(v.trip),
                    "lat": _opt(pos, "latitude") if v.HasField("position") else None,
                    "lon": _opt(pos, "longitude") if v.HasField("position") else None,
                    "bearing": _opt(pos, "bearing"),
                    "speed": _opt(pos, "speed"),
                    "odometer": _opt(pos, "odometer"),
                    "current_stop_sequence": _opt(v, "current_stop_sequence"),
                    "stop_id": _opt(v, "stop_id"),
                    "current_status": _enum(v, "current_status"),
                    "congestion_level": _enum(v, "congestion_level"),
                    "occupancy_status": _enum(v, "occupancy_status"),
                    "occupancy_percentage": _opt(v, "occupancy_percentage"),
                }
            )

        if entity.HasField("trip_update"):
            tu = entity.trip_update
            base = {
                "entity_id": entity.id,
                **_trip(tu.trip),
                "vehicle_id": _opt(tu.vehicle, "id"),
                "trip_update_ts": _opt(tu, "timestamp"),
                "trip_delay": _opt(tu, "delay"),
            }
            out.trip_updates.append({**base, "stop_time_update_count": len(tu.stop_time_update)})
            for stu in tu.stop_time_update:
                out.trip_update_stops.append(
                    {
                        **base,
                        "stop_sequence": _opt(stu, "stop_sequence"),
                        "stop_id": _opt(stu, "stop_id"),
                        "arrival_time": _opt(stu.arrival, "time"),
                        "arrival_delay": _opt(stu.arrival, "delay"),
                        "arrival_uncertainty": _opt(stu.arrival, "uncertainty"),
                        "departure_time": _opt(stu.departure, "time"),
                        "departure_delay": _opt(stu.departure, "delay"),
                        "departure_uncertainty": _opt(stu.departure, "uncertainty"),
                        "stop_schedule_relationship": _enum(stu, "schedule_relationship"),
                    }
                )

        if entity.HasField("alert"):
            a = entity.alert
            out.alerts.append(
                {
                    "entity_id": entity.id,
                    "active_periods": [(_opt(p, "start"), _opt(p, "end")) for p in a.active_period],
                    "cause": _enum(a, "cause"),
                    "effect": _enum(a, "effect"),
                    "header": _text(a.header_text),
                    "description": _text(a.description_text),
                    "url": _text(a.url),
                    "route_ids": sorted(
                        {e.route_id for e in a.informed_entity if e.HasField("route_id")}
                    ),
                    "stop_ids": sorted(
                        {e.stop_id for e in a.informed_entity if e.HasField("stop_id")}
                    ),
                    "trip_ids": sorted(
                        {
                            e.trip.trip_id
                            for e in a.informed_entity
                            if e.HasField("trip") and e.trip.HasField("trip_id")
                        }
                    ),
                }
            )
    return out


def field_population(rows: list[Row]) -> dict[str, float]:
    """Share of rows in which each field is present (not None)."""
    if not rows:
        return {}
    keys = rows[0].keys()
    return {k: sum(r[k] is not None for r in rows) / len(rows) for k in keys}
