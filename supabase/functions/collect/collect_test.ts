// Run with: deno test --allow-read supabase/functions/collect/
import { assertEquals, assertThrows } from "jsr:@std/assert@1";
import { summariseFeed } from "./protobuf.ts";
import { scanZip } from "./zipscan.ts";

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
