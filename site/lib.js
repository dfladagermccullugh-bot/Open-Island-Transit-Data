// Shared loaders for the site. Files come from the repository's `data`
// branch, copied into data/ when the site is built. Missing files (no data
// yet) give empty results instead of errors.

export async function csv(d3, path) {
  try {
    const rows = await d3.csv(path, d3.autoType);
    return rows;
  } catch (e) {
    return [];
  }
}

export async function json(path) {
  try {
    const r = await fetch(path);
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;
  }
}

// One place for the addresses the pages link to and read from. The key is a
// publishable one: it can only read the collector's public tables.
export const REPO_URL = "https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data";
export const DATA_URL = `${REPO_URL}/blob/data`;
export const RELEASES_URL = `${REPO_URL}/releases`;
export const STATUS_URL = "https://ekczqlmunyumtgtthppb.supabase.co/rest/v1/status_now";
export const PUBLIC_KEY = "sb_publishable_AWlvBYVrlTz7dEaf7RwwzA_5zRN3af-";

// The collector's live status, or null if it cannot be read within 8 seconds.
export async function liveStatus() {
  try {
    const r = await fetch(STATUS_URL, {headers: {apikey: PUBLIC_KEY}, signal: AbortSignal.timeout(8000)});
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;
  }
}

// Rows of a daily table for days on which buses were scheduled to run.
// A holiday with no service is archived like any other day but left out of
// trend lines, where it would look like a day of lost buses.
export function serviceDays(rows) {
  return rows.filter(d => d.service_expected !== false && d.service_expected !== "False");
}

// "2026-11-20" from a date string or a Date read from a CSV.
export function isoDate(d) {
  if (d == null) return null;
  return d instanceof Date ? d.toISOString().slice(0, 10) : String(d).slice(0, 10);
}

// Trend charts show the most recent service days unless the reader asks for
// all of them; with more than a month on screen the dots are left out and the
// line carries the chart.
export const RECENT_DAYS = 90;
export const DOTS_UP_TO = 31;

export function recentDays(rows, all = false) {
  return all || rows.length <= RECENT_DAYS ? rows : rows.slice(-RECENT_DAYS);
}

// Options for the date axis of a daily chart: {x, marginBottom}. Ticks sit on
// days that have data, at most `maxTicks` of them and fewer on a narrow chart
// (`chartWidth` in pixels), so labels do not run together. When the days span two
// calendar years, the first tick and the first tick of each new year carry
// the year on a second line, so "Jan 4" cannot be read as the wrong year.
export function dateAxis(dates, maxTicks = 8, chartWidth = 960) {
  const sorted = [...dates].sort((a, b) => a - b);
  const fit = Math.max(2, Math.min(maxTicks, Math.floor(chartWidth / 75)));
  const step = Math.max(1, Math.ceil(sorted.length / fit));
  const ticks = sorted.filter((_, i) => i % step === 0);
  const spansYears = new Set(sorted.map(d => d.getUTCFullYear())).size > 1;
  const monthDay = d => d.toLocaleDateString("en-US", {month: "short", day: "numeric", timeZone: "UTC"});
  const tickFormat = d => {
    const i = ticks.findIndex(t => +t === +d);
    const firstOfYear = i <= 0 || ticks[i - 1].getUTCFullYear() !== d.getUTCFullYear();
    return spansYears && firstOfYear ? `${monthDay(d)}\n${d.getUTCFullYear()}` : monthDay(d);
  };
  return {x: {type: "utc", label: null, ticks, tickFormat}, marginBottom: spansYears ? 44 : 30};
}

export function count(n) {
  return n == null || Number.isNaN(n) ? "–" : Number(n).toLocaleString("en-US");
}

export function pct(x, digits = 0) {
  return x == null || Number.isNaN(x) ? "–" : `${(100 * x).toFixed(digits)}%`;
}

export function num(x, digits = 0) {
  return x == null || Number.isNaN(x) ? "–" : Number(x).toFixed(digits);
}

export function routeLabel(routes, id) {
  const r = routes && routes[id];
  if (!r) return id;
  return r.short_name ? `${r.short_name} ${r.long_name}` : r.long_name || id;
}

export const SERIES = "var(--series-1)";

// Plain route name: "Route 60 · Freeland - Langley - Clinton", or the long
// name for routes without a number.
export function routeTitle(r) {
  if (!r) return "";
  return r.short_name ? `Route ${r.short_name}` : r.long_name || r.route_id || r.name;
}

export function routeSub(r) {
  return r && r.short_name ? r.long_name : "";
}

// Order routes as the timetables do: 1, 1C, 2, 2C, ..., 10, 58, 411C; unnamed last.
export function routeOrder(a, b) {
  const key = r => {
    const n = (r.short_name || "").match(/\d+/);
    return [n ? 0 : 1, n ? +n[0] : 0, r.short_name || r.long_name || ""];
  };
  const [x, y] = [key(a), key(b)];
  return x[0] - y[0] || x[1] - y[1] || String(x[2]).localeCompare(String(y[2]));
}

// URL-friendly key for a route: its number, else its id.
export function routeSlug(r, id) {
  return (r && r.short_name ? r.short_name : id).toLowerCase();
}

// On-time bands used for colors on the map and route pages. Each band
// always appears with its label, never by color alone.
export const MIN_OBSERVED = 10;
export const BANDS = [
  {key: "good", min: 0.8, label: "80% or more on time", color: "var(--status-good)"},
  {key: "warning", min: 0.6, label: "60 to 79% on time", color: "var(--status-warning)"},
  {key: "critical", min: 0, label: "Under 60% on time", color: "var(--status-critical)"},
];
export const NO_BAND = {key: "none", label: "Too few departures seen", color: "var(--status-none)"};

export function band(share, observed) {
  if (share == null || observed == null || observed < MIN_OBSERVED) return NO_BAND;
  return BANDS.find(b => share >= b.min);
}

// "3.5 min late", "1.2 min early", "on schedule".
export function lateness(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "–";
  const m = Math.abs(seconds) / 60;
  if (m < 0.25) return "on schedule";
  return `${m.toFixed(1)} min ${seconds > 0 ? "late" : "early"}`;
}

export function plural(n, word, many = word + "s") {
  return `${n.toLocaleString("en-US")} ${n === 1 ? word : many}`;
}

// "November 20, 2026" from "2026-11-20" or from a Date read from a CSV.
export function longDate(value) {
  const iso = isoDate(value);
  if (!iso) return "";
  const d = new Date(`${iso}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString("en-US", {month: "long", day: "numeric", year: "numeric", timeZone: "UTC"});
}

// The route map file for a schedule version (maps/<timepoints key>.json).
export async function routeMap(timepointsKey) {
  return timepointsKey ? json(`data/maps/${timepointsKey}.json`) : null;
}
