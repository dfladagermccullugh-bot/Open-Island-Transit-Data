// Open Island Transit Data: collector.
//
// Invoked by pg_cron (see supabase/migrations/0002_schedule.sql).
//   { kind: "realtime", targets: ["vehiclePositions", "tripUpdates"], scheduled_at }
//   { kind: "static",   targets: ["passio"],                          scheduled_at }
//   { kind: "prune" }   delete days already archived on GitHub (see prune.ts)
//
// For every target it makes one HTTP request, stores the exact response bytes
// and logs the attempt, whatever the outcome. It never retries within a tick:
// the next tick is 20 seconds away, and a failed attempt is itself data.

import { encodeBase64 } from "jsr:@std/encoding@1/base64";
import { summariseFeed } from "./protobuf.ts";
import { scanZip } from "./zipscan.ts";
import { assetUrl, daysToPrune, type Manifest } from "./prune.ts";

const VERSION = "collect/0.2.0";
const USER_AGENT =
  "OpenIslandTransitData/0.1 (+https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data)";

const REPO = "dfladagermccullugh-bot/Open-Island-Transit-Data";
const MANIFEST_URL = `https://raw.githubusercontent.com/${REPO}/data/manifest/raw.json`;

const REALTIME_BASE = "https://passio3.com/islandtransit/passioTransit/gtfs/realtime/";
const REALTIME_FEEDS = new Set(["vehiclePositions", "tripUpdates", "serviceAlerts"]);
const STATIC_URLS: Record<string, string> = {
  passio: "https://passio3.com/islandtransit/passioTransit/gtfs/google_transit.zip",
  trillium: "https://data.trilliumtransit.com/gtfs/islandtransit-wa-us/islandtransit-wa-us.zip",
};

const REALTIME_TIMEOUT_MS = 8_000;
const STATIC_TIMEOUT_MS = 60_000;

type Json = Record<string, unknown>;

interface Fetched {
  requestedAt: Date;
  respondedAt: Date | null;
  status: number | null;
  body: Uint8Array | null;
  errorClass: string | null;
  errorMsg: string | null;
}

async function timedFetch(url: string, timeoutMs: number): Promise<Fetched> {
  const requestedAt = new Date();
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": USER_AGENT, "Accept": "*/*" },
      signal: AbortSignal.timeout(timeoutMs),
      redirect: "follow",
    });
    const body = new Uint8Array(await res.arrayBuffer());
    return {
      requestedAt,
      respondedAt: new Date(),
      status: res.status,
      body,
      errorClass: res.status === 200 ? null : "http",
      errorMsg: res.status === 200 ? null : `HTTP ${res.status}`,
    };
  } catch (err) {
    const e = err as Error;
    const timedOut = e.name === "TimeoutError" || e.name === "AbortError";
    return {
      requestedAt,
      respondedAt: null,
      status: null,
      body: null,
      errorClass: timedOut ? "timeout" : "network",
      errorMsg: `${e.name}: ${e.message}`.slice(0, 500),
    };
  }
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes as BufferSource);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

async function gzip(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

const iso = (seconds: number | null) => (seconds === null ? null : new Date(seconds * 1000).toISOString());

async function collectRealtime(feed: string, scheduledAt: string | null): Promise<Json> {
  const f = await timedFetch(REALTIME_BASE + feed, REALTIME_TIMEOUT_MS);
  const row: Json = {
    feed,
    scheduled_at: scheduledAt,
    requested_at: f.requestedAt.toISOString(),
    responded_at: f.respondedAt?.toISOString() ?? null,
    latency_ms: f.respondedAt ? f.respondedAt.getTime() - f.requestedAt.getTime() : null,
    http_status: f.status,
    bytes: f.body?.length ?? null,
    sha256: null,
    header_ts: null,
    entity_count: null,
    newest_entity_ts: null,
    error_class: f.errorClass,
    error_msg: f.errorMsg,
    collector_version: VERSION,
  };
  if (f.body) {
    // Store the body whatever the status: an error page is evidence too.
    row.sha256 = await sha256Hex(f.body);
    row.body_gz_b64 = encodeBase64(await gzip(f.body));
    if (f.status === 200) {
      try {
        const s = summariseFeed(f.body);
        row.header_ts = iso(s.headerTs);
        row.entity_count = s.entityCount;
        row.newest_entity_ts = iso(s.newestEntityTs);
      } catch (err) {
        row.error_class = "decode";
        row.error_msg = (err as Error).message.slice(0, 500);
      }
    }
  }
  return row;
}

async function collectStatic(source: string): Promise<Json> {
  const url = STATIC_URLS[source];
  const f = await timedFetch(url, STATIC_TIMEOUT_MS);
  const row: Json = {
    source,
    url,
    requested_at: f.requestedAt.toISOString(),
    responded_at: f.respondedAt?.toISOString() ?? null,
    latency_ms: f.respondedAt ? f.respondedAt.getTime() - f.requestedAt.getTime() : null,
    http_status: f.status,
    bytes: f.body?.length ?? null,
    sha256: null,
    zip_has_directory: null,
    files: null,
    error_class: f.errorClass,
    error_msg: f.errorMsg,
    collector_version: VERSION,
    body_b64: null,
  };
  if (f.body) {
    row.sha256 = await sha256Hex(f.body);
    row.body_b64 = encodeBase64(f.body);
    if (f.status === 200) {
      try {
        const z = scanZip(f.body);
        row.zip_has_directory = z.hasDirectory;
        row.files = z.files;
      } catch (err) {
        row.error_class = "decode";
        row.error_msg = (err as Error).message.slice(0, 500);
      }
    }
  }
  return row;
}

async function rpc(name: string, args: Json): Promise<unknown> {
  const base = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!base || !key) throw new Error("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set");
  const res = await fetch(`${base}/rest/v1/rpc/${name}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "apikey": key,
      "Authorization": `Bearer ${key}`,
    },
    body: JSON.stringify(args),
  });
  if (!res.ok) throw new Error(`rpc ${name} failed: HTTP ${res.status} ${await res.text()}`);
  return await res.json();
}

async function assetConfirmed(tag: string, name: string, bytes: number): Promise<boolean> {
  const res = await fetch(assetUrl(REPO, tag, name), {
    method: "HEAD",
    headers: { "User-Agent": USER_AGENT },
    redirect: "follow",
    signal: AbortSignal.timeout(20_000),
  });
  await res.body?.cancel();
  if (!res.ok) return false;
  const length = res.headers.get("content-length");
  return length === null || Number(length) === bytes;
}

async function prune(): Promise<Json> {
  const res = await fetch(MANIFEST_URL, {
    headers: { "User-Agent": USER_AGENT },
    signal: AbortSignal.timeout(20_000),
  });
  if (res.status === 404) {
    await res.body?.cancel();
    return { ok: true, note: "no archive manifest yet", days: [] };
  }
  if (!res.ok) throw new Error(`manifest: HTTP ${res.status}`);
  const manifest = (await res.json()) as Manifest;

  const health = (await rpc("collector_health", {})) as { oldest_raw_at: string | null }[];
  const days = daysToPrune(manifest, health[0]?.oldest_raw_at ?? null, new Date());

  const results: Json[] = [];
  for (const day of days) {
    const entry = manifest.days[day];
    try {
      for (const f of entry.files) {
        if (!(await assetConfirmed(entry.release_tag!, f.name, f.bytes))) {
          throw new Error(`archive file not confirmed: ${f.name}`);
        }
      }
      const deleted = await rpc("prune_raw", {
        p_date: day,
        p_raw_rows: entry.raw_rows,
        p_digest: entry.digest,
      });
      const archivedStatic = (entry.static ?? []).filter((s) => s.file && s.sha256);
      const staticCleared = archivedStatic.length
        ? await rpc("prune_static", {
          p_ids: archivedStatic.map((s) => s.static_fetch_id),
          p_sha256: archivedStatic.map((s) => s.sha256),
        })
        : 0;
      results.push({ day, deleted, static_cleared: staticCleared });
    } catch (err) {
      results.push({ day, error: (err as Error).message.slice(0, 300) });
    }
  }
  return { ok: true, days: results };
}

function sameSecret(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// The shared secret lives only in the database (Vault). The scheduler sends
// it with every tick; the function asks the database whether it is right and
// remembers a confirmed value for as long as this worker stays warm.
let confirmedSecret: string | null = null;

async function authorised(given: string): Promise<boolean> {
  if (given.length < 32) return false;
  if (confirmedSecret !== null && sameSecret(confirmedSecret, given)) return true;
  if ((await rpc("collector_secret_ok", { p_secret: given })) === true) {
    confirmedSecret = given;
    return true;
  }
  return false;
}

Deno.serve(async (req: Request) => {
  const given = req.headers.get("x-collector-secret") ?? "";
  try {
    if (!(await authorised(given))) return new Response("forbidden", { status: 403 });
  } catch (err) {
    console.error(err);
    return new Response("auth check failed", { status: 500 });
  }

  let payload: { kind?: string; targets?: string[]; scheduled_at?: string };
  try {
    payload = await req.json();
  } catch {
    return new Response("bad request", { status: 400 });
  }
  const targets = Array.isArray(payload.targets) ? payload.targets : [];
  const scheduledAt = payload.scheduled_at ?? null;

  try {
    if (payload.kind === "realtime") {
      const feeds = targets.filter((t) => REALTIME_FEEDS.has(t));
      const rows = await Promise.all(feeds.map((feed) => collectRealtime(feed, scheduledAt)));
      if (rows.length) await rpc("record_fetches", { p_rows: rows });
      return Response.json({ ok: true, recorded: rows.length });
    }
    if (payload.kind === "static") {
      const sources = targets.filter((t) => t in STATIC_URLS);
      for (const source of sources) {
        await rpc("record_static", { p_row: await collectStatic(source) });
      }
      return Response.json({ ok: true, recorded: sources.length });
    }
    if (payload.kind === "prune") {
      return Response.json(await prune());
    }
    return new Response("unknown kind", { status: 400 });
  } catch (err) {
    console.error(err);
    return Response.json({ ok: false, error: (err as Error).message }, { status: 500 });
  }
});
