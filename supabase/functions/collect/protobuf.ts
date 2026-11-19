// Minimal protobuf wire-format reader: just enough to summarise a GTFS-Realtime
// FeedMessage without decoding it fully. The raw bytes are stored untouched;
// this summary only fills fetch_log (header time, entity count, newest
// entity timestamp). Full decoding happens later in the Python pipeline.
//
// Field numbers (gtfs-realtime.proto):
//   FeedMessage:     1 header, 2 entity (repeated)
//   FeedHeader:      3 timestamp
//   FeedEntity:      3 trip_update, 4 vehicle, 5 alert
//   TripUpdate:      4 timestamp
//   VehiclePosition: 5 timestamp

export interface FeedSummary {
  headerTs: number | null; // POSIX seconds
  entityCount: number;
  newestEntityTs: number | null; // POSIX seconds
}

interface Field {
  num: number;
  wire: number;
  value: number; // varint value (wire 0)
  bytes: Uint8Array | null; // payload (wire 2)
}

function readVarint(buf: Uint8Array, pos: number): [number, number] {
  let result = 0;
  let mul = 1;
  for (let i = 0; i < 10; i++) {
    if (pos >= buf.length) throw new Error("truncated varint");
    const b = buf[pos++];
    result += (b & 0x7f) * mul;
    if ((b & 0x80) === 0) return [result, pos];
    mul *= 128;
  }
  throw new Error("varint too long");
}

function* fields(buf: Uint8Array): Generator<Field> {
  let pos = 0;
  while (pos < buf.length) {
    let key: number;
    [key, pos] = readVarint(buf, pos);
    const num = Math.floor(key / 8);
    const wire = key & 7;
    if (num === 0) throw new Error("invalid field number 0");
    if (wire === 0) {
      let v: number;
      [v, pos] = readVarint(buf, pos);
      yield { num, wire, value: v, bytes: null };
    } else if (wire === 2) {
      let len: number;
      [len, pos] = readVarint(buf, pos);
      if (pos + len > buf.length) throw new Error("truncated length-delimited field");
      yield { num, wire, value: 0, bytes: buf.subarray(pos, pos + len) };
      pos += len;
    } else if (wire === 1) {
      if (pos + 8 > buf.length) throw new Error("truncated fixed64");
      pos += 8;
      yield { num, wire, value: 0, bytes: null };
    } else if (wire === 5) {
      if (pos + 4 > buf.length) throw new Error("truncated fixed32");
      pos += 4;
      yield { num, wire, value: 0, bytes: null };
    } else {
      throw new Error(`unsupported wire type ${wire}`);
    }
  }
}

function varintField(buf: Uint8Array, wanted: number): number | null {
  for (const f of fields(buf)) {
    if (f.num === wanted && f.wire === 0) return f.value;
  }
  return null;
}

export function summariseFeed(buf: Uint8Array): FeedSummary {
  let headerTs: number | null = null;
  let entityCount = 0;
  let newest: number | null = null;
  let sawHeader = false;

  for (const f of fields(buf)) {
    if (f.num === 1 && f.bytes) {
      sawHeader = true;
      headerTs = varintField(f.bytes, 3);
    } else if (f.num === 2 && f.bytes) {
      entityCount++;
      for (const e of fields(f.bytes)) {
        if (!e.bytes) continue;
        let ts: number | null = null;
        if (e.num === 3) ts = varintField(e.bytes, 4); // trip_update.timestamp
        else if (e.num === 4) ts = varintField(e.bytes, 5); // vehicle.timestamp
        if (ts !== null && ts > 0 && (newest === null || ts > newest)) newest = ts;
      }
    }
  }
  if (!sawHeader) throw new Error("no FeedHeader: not a GTFS-Realtime message");
  return { headerTs: headerTs && headerTs > 0 ? headerTs : null, entityCount, newestEntityTs: newest };
}
