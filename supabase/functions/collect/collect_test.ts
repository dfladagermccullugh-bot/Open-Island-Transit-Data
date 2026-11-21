// Run with: deno test --allow-read supabase/functions/collect/
import { assertEquals, assertThrows } from "jsr:@std/assert@1";
import { summariseFeed } from "./protobuf.ts";
import { scanZip } from "./zipscan.ts";
import {
  daysToPrune,
  type Manifest,
  pacificDate,
  sizeConfirmed,
  totalFromContentRange,
} from "./prune.ts";

const fixture = (name: string) =>
  Deno.readFileSync(new URL(`../../../tests/fixtures/${name}`, import.meta.url));

Deno.test("empty after-hours feed: header only", () => {
  const s = summariseFeed(fixture("empty_feed_20261118.pb"));
  assertEquals(s.entityCount, 0);
  assertEquals(s.headerTs, 1795071012);
  assertEquals(s.newestEntityTs, null);
});

Deno.test("synthetic feed: counts entities and finds the newest timestamp", () => {
  const s = summariseFeed(fixture("synthetic_feed.pb"));
  assertEquals(s.entityCount, 3);
  assertEquals(s.headerTs, 1795100000);
  // Two vehicles (…990, …996) and one trip update (…998).
  assertEquals(s.newestEntityTs, 1795099998);
});

Deno.test("an HTML error page is not a feed", () => {
  const html = new TextEncoder().encode("<html><body>502 Bad Gateway</body></html>");
  assertThrows(() => summariseFeed(html));
});

Deno.test("truncated message is rejected", () => {
  const full = fixture("synthetic_feed.pb");
  assertThrows(() => summariseFeed(full.subarray(0, full.length - 7)));
});

Deno.test("complete zip: directory present, all entries listed", () => {
  const z = scanZip(fixture("gtfs_complete.zip"));
  assertEquals(z.hasDirectory, true);
  assertEquals(z.files, [
    "agency.txt",
    "stops.txt",
    "routes.txt",
    "trips.txt",
    "stop_times.txt",
    "calendar.txt",
  ]);
});

Deno.test("zip cut off before its directory: flagged, entries still listed", () => {
  const z = scanZip(fixture("gtfs_no_directory.zip"));
  assertEquals(z.hasDirectory, false);
  assertEquals(z.files, ["agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"]);
});

Deno.test("real Island Transit vehicle positions (2026-11-19 07:35 PT)", () => {
  const s = summariseFeed(fixture("islandtransit_vehiclePositions_20261119T0731.pb"));
  assertEquals(s.entityCount, 20);
  // Newest vehicle timestamp is at or just before the header time.
  const lag = s.headerTs! - s.newestEntityTs!;
  assertEquals(lag >= 0 && lag < 60, true);
});

Deno.test("real Island Transit trip updates (2026-11-19 07:35 PT)", () => {
  const s = summariseFeed(fixture("islandtransit_tripUpdates_20261119T0731.pb"));
  assertEquals(s.entityCount > 20, true);
  assertEquals(s.newestEntityTs !== null, true);
});

const entry = (day: string, tag: string | null = "raw-2026-11") => ({
  service_date: day,
  raw_rows: 10,
  digest: "x",
  release_tag: tag ?? undefined,
  files: [{ name: `raw-${day}.parquet`, bytes: 1, sha256: "y" }],
});

Deno.test("pacificDate follows Pacific midnight, not UTC", () => {
  assertEquals(pacificDate(new Date("2026-11-20T07:59:59Z")), "2026-11-19");
  assertEquals(pacificDate(new Date("2026-11-20T08:00:00Z")), "2026-11-20");
  assertEquals(pacificDate(new Date("2027-07-01T06:59:59Z")), "2027-06-30"); // PDT
});

Deno.test("prune: only archived days at least 3 days old", () => {
  const manifest: Manifest = {
    days: {
      "2026-11-19": entry("2026-11-19"),
      "2026-11-20": entry("2026-11-20"),
      // 2026-11-21 was never archived
      "2026-11-22": entry("2026-11-22"),
      "2026-11-23": entry("2026-11-23"),
      "2026-11-24": entry("2026-11-24"),
    },
  };
  const now = new Date("2026-11-25T20:00:00Z"); // Nov 25, noon Pacific
  assertEquals(daysToPrune(manifest, "2026-11-19T21:24:41Z", now), [
    "2026-11-19",
    "2026-11-20",
    "2026-11-22",
  ]);
  // Older days already pruned: start from the oldest day that still has data.
  assertEquals(daysToPrune(manifest, "2026-11-22T09:00:00Z", now), ["2026-11-22"]);
});

Deno.test("prune: nothing without stored data, a manifest entry, or a release tag", () => {
  const now = new Date("2026-11-25T20:00:00Z");
  assertEquals(daysToPrune({ days: {} }, "2026-11-19T21:24:41Z", now), []);
  assertEquals(daysToPrune({ days: { "2026-11-19": entry("2026-11-19") } }, null, now), []);
  assertEquals(
    daysToPrune({ days: { "2026-11-19": entry("2026-11-19", null) } }, "2026-11-19T21:24:41Z", now),
    [],
  );
});

Deno.test("prune: capped per run", () => {
  const days: Manifest["days"] = {};
  for (let d = 1; d <= 28; d++) {
    const day = `2026-10-${String(d).padStart(2, "0")}`;
    days[day] = entry(day, "raw-2026-10");
  }
  const got = daysToPrune({ days }, "2026-10-01T10:00:00Z", new Date("2026-11-25T20:00:00Z"));
  assertEquals(got.length, 14);
  assertEquals(got[0], "2026-10-01");
});

Deno.test("content-range total is read; other forms give null", () => {
  assertEquals(totalFromContentRange("bytes 0-0/1158375"), 1158375);
  assertEquals(totalFromContentRange("bytes 0-0/*"), null);
  assertEquals(totalFromContentRange(null), null);
  assertEquals(totalFromContentRange("1158375"), null);
});

Deno.test("a file is confirmed only by a 206 stating the recorded size", () => {
  assertEquals(sizeConfirmed(206, "bytes 0-0/1158375", 1158375), true);
  assertEquals(sizeConfirmed(206, "bytes 0-0/1158374", 1158375), false);
  assertEquals(sizeConfirmed(200, null, 1158375), false); // no size stated
  assertEquals(sizeConfirmed(401, null, 1158375), false);
  assertEquals(sizeConfirmed(404, "bytes 0-0/1158375", 1158375), false);
});
