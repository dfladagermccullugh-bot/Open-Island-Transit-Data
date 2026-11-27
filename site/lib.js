// Shared by every page: loading the published files, formatting numbers and
// dates, naming routes, the on-time bands, and a few small page parts.
//
// Files come from the repository's `data` branch, copied into data/ when the
// site is built. A missing file (no data yet) gives an empty result instead
// of an error, and every formatter prints a dash for a missing value, so a
// page with a hole in its data still reads as a page.

// --- addresses ---------------------------------------------------------------

// One place for the addresses the pages link to and read from. The key is a
// publishable one: it can only read the collector's public tables.
export const REPO_URL = "https://github.com/dfladagermccullugh-bot/Open-Island-Transit-Data";
// A file on the data branch, shown on GitHub with its history.
export const DATA_URL = `${REPO_URL}/blob/data`;
// The same file as plain bytes, for downloading or reading from code.
export const DATA_RAW_URL = "https://raw.githubusercontent.com/dfladagermccullugh-bot/Open-Island-Transit-Data/data";
export const RELEASES_URL = `${REPO_URL}/releases`;
export const STATUS_URL = "https://ekczqlmunyumtgtthppb.supabase.co/rest/v1/status_now";
export const PUBLIC_KEY = "sb_publishable_AWlvBYVrlTz7dEaf7RwwzA_5zRN3af-";

// Below this width a chart or the page is laid out for a phone. The same
// number is $bp-narrow in _tokens.scss.
export const NARROW = 640;

// --- loading -----------------------------------------------------------------

export async function csv(d3, path) {
  try {
    return await d3.csv(path, d3.autoType);
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

// The collector's live status, or null if it cannot be read within 8 seconds.
export async function liveStatus() {
  try {
    const r = await fetch(STATUS_URL, {headers: {apikey: PUBLIC_KEY}, signal: AbortSignal.timeout(8000)});
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;
  }
}

// The route map file for a schedule version (maps/<timepoints key>.json).
export async function routeMap(timepointsKey) {
  return timepointsKey ? json(`data/maps/${timepointsKey}.json`) : null;
}

// --- days --------------------------------------------------------------------

// True for the ways a table can say "no": the daily tables were first written
// with Python's "False", and are now written with "false".
export function isFalse(value) {
  return value === false || String(value).toLowerCase() === "false";
}

// Rows of a daily table for days on which buses were scheduled to run.
// A holiday with no service is archived like any other day but left out of
// trend lines, where it would look like a day of lost buses.
export function serviceDays(rows) {
  return rows.filter(d => !isFalse(d.service_expected));
}

// "2026-11-20" from a date string or a Date read from a CSV.
export function isoDate(d) {
  if (d == null || d === "") return null;
  if (d instanceof Date) return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
  return String(d).slice(0, 10);
}

// A service day as a moment in time: noon UTC of that date, which is the
// same calendar day in every time zone a reader is likely to be in.
export function noonUTC(value) {
  const iso = isoDate(value);
  if (!iso) return null;
  const d = new Date(`${iso}T12:00:00Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

// Rows with `day` ("2026-11-20") and `date` (for a chart axis) added; rows
// whose date cannot be read are dropped.
export function withDates(rows, key = "service_date") {
  return rows
    .map(d => ({...d, day: isoDate(d[key]), date: noonUTC(d[key])}))
    .filter(d => d.date != null);
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

// --- numbers and words -------------------------------------------------------

const DASH = "–";
const missing = x => x == null || x === "" || Number.isNaN(+x);

// 2,887
export function count(n) {
  return missing(n) ? DASH : Number(n).toLocaleString("en-US");
}

// 67% from 0.67. A value that rounds to zero never carries a minus sign.
export function pct(x, digits = 0) {
  if (missing(x)) return DASH;
  const text = (100 * x).toFixed(digits);
  return `${+text === 0 ? text.replace("-", "") : text}%`;
}

// 3.8, with thousands separators once the number is large enough to need them.
export function num(x, digits = 0) {
  if (missing(x)) return DASH;
  return Number(x).toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits});
}

// "1 second", "4.1 seconds", "83 minutes": a number with its unit in words.
export function unit(x, word, digits = 0, many = `${word}s`) {
  if (missing(x)) return DASH;
  const text = num(x, digits);
  return `${text} ${+Number(x).toFixed(digits) === 1 ? word : many}`;
}

// "2.6 MB" from a count of bytes.
export function megabytes(bytes, digits = 1) {
  return missing(bytes) ? DASH : `${num(bytes / 1e6, digits)} MB`;
}

// "3.6 KB", "0.75 MB", "2.6 MB": the size of one file, in the unit that fits.
export function fileSize(bytes) {
  if (missing(bytes)) return DASH;
  if (bytes < 1e3) return `${num(bytes)} bytes`;
  if (bytes < 1e6) return `${num(bytes / 1e3, bytes < 1e4 ? 1 : 0)} KB`;
  return `${num(bytes / 1e6, bytes < 1e7 ? 2 : 1)} MB`;
}

// "3.5 min late", "1.2 min early", "on schedule".
export function lateness(seconds) {
  if (missing(seconds)) return DASH;
  const m = Math.abs(seconds) / 60;
  if (m < 0.25) return "on schedule";
  return `${m.toFixed(1)} min ${seconds > 0 ? "late" : "early"}`;
}

// "1 departure", "24 departures".
export function plural(n, word, many = `${word}s`) {
  if (missing(n)) return `no ${many}`;
  return `${Number(n).toLocaleString("en-US")} ${+n === 1 ? word : many}`;
}

// "November 20, 2026" from "2026-11-20" or from a Date read from a CSV.
export function longDate(value) {
  const d = noonUTC(value);
  return d ? d.toLocaleDateString("en-US", {month: "long", day: "numeric", year: "numeric", timeZone: "UTC"}) : "";
}

// "6 AM", "12 PM", "3 PM" from an hour of the day.
export function hourLabel(hour) {
  return `${(+hour % 12) || 12} ${+hour < 12 ? "AM" : "PM"}`;
}

// "3:35 AM" Pacific from a timestamp.
export function pacificClock(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleTimeString("en-US", {hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles"});
}

// --- routes ------------------------------------------------------------------

// Passio names the three NAS Whidbey shuttles "Naswi Am", "Naswi Pm" and
// "Naswi Wkend"; readers know them as the NAS Whidbey shuttle.
const LONG_NAMES = {"naswi am": "NAS Whidbey, morning", "naswi pm": "NAS Whidbey, afternoon", "naswi wkend": "NAS Whidbey, weekend"};
export function longName(name) {
  return LONG_NAMES[String(name ?? "").trim().toLowerCase()] ?? name;
}

// "60 Freeland - Langley - Clinton" from a map of route ids to names.
export function routeLabel(routes, id) {
  const r = routes && routes[id];
  if (!r) return String(id);
  return r.short_name ? `${r.short_name} ${r.long_name ?? ""}`.trim() : longName(r.long_name) || String(id);
}

// "Route 60", or the long name for routes without a number.
export function routeTitle(r) {
  if (!r) return "";
  return r.short_name ? `Route ${r.short_name}` : longName(r.long_name) || r.route_id || longName(r.name) || "";
}

// "Freeland - Langley - Clinton" for a numbered route, else nothing.
export function routeSub(r) {
  return r && r.short_name ? r.long_name ?? "" : "";
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
  return String(r && r.short_name ? r.short_name : id).toLowerCase();
}

// The route the page address names (routes.html#60), or null.
export function routeFromHash(routes, hash = location.hash) {
  let wanted;
  try {
    wanted = decodeURIComponent(hash.replace(/^#/, "")).toLowerCase();
  } catch (e) {
    return null;
  }
  if (!wanted) return null;
  const r = routes.find(r => routeSlug(r, r.route_id) === wanted);
  return r ? r.route_id : null;
}

// --- on-time bands -----------------------------------------------------------

// Bands used on the map, the route pages and the hour chart. A band is never
// shown by color alone: it always carries its shape, and its label or its
// number is always in reach.
export const MIN_OBSERVED = 10;
export const BANDS = [
  {key: "good", rank: 3, min: 0.8, label: "80% or more on time", shape: "circle", color: "var(--status-good)"},
  {key: "warning", rank: 2, min: 0.6, label: "60 to 79% on time", shape: "triangle", color: "var(--status-warning)"},
  {key: "critical", rank: 1, min: 0, label: "Under 60% on time", shape: "square", color: "var(--status-critical)"},
];
export const NO_BAND = {key: "none", rank: 0, label: "Too few departures seen", shape: "ring", color: "var(--status-none)"};

export function band(share, observed) {
  if (missing(share) || missing(observed) || observed < MIN_OBSERVED) return NO_BAND;
  return BANDS.find(b => share >= b.min) ?? NO_BAND;
}

const SHAPES = {
  circle: '<circle cx="8" cy="8" r="6"/>',
  triangle: '<path d="M8 1.6 14.9 13.6H1.1z"/>',
  square: '<rect x="2.4" y="2.4" width="11.2" height="11.2" rx="1.6"/>',
  ring: '<circle cx="8" cy="8" r="4.75" fill="none" stroke-width="2.5"/>',
};

// The band's shape as SVG text, colored by the band-<key> class.
export function bandIconHTML(b, size = 14) {
  return `<svg class="band-icon band-${b.key}" width="${size}" height="${size}" viewBox="0 0 16 16" aria-hidden="true" focusable="false">${SHAPES[b.shape]}</svg>`;
}

export function bandIcon(b, size = 14) {
  return fragment(bandIconHTML(b, size)).firstChild;
}

// Shape and label together: "● 80% or more on time".
export function bandTag(b) {
  return h("span", {class: "band-tag"}, bandIcon(b), b.label);
}

// The key to the bands, as a list a screen reader can read.
export function bandLegend({title = null} = {}) {
  return h("div", {class: "band-legend"},
    title ? h("p", {class: "band-legend-title"}, title) : null,
    h("ul", {}, ...[...BANDS, NO_BAND].map(b => h("li", {}, bandIcon(b), b.label))));
}

// --- small page parts --------------------------------------------------------

function fragment(markup) {
  const t = document.createElement("template");
  t.innerHTML = markup.trim();
  return t.content;
}

// h("p", {class: "note"}, "text", node): an element with attributes and
// children. Text is always inserted as text, never as markup.
export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs ?? {})) {
    if (v == null || v === false) continue;
    node.setAttribute(k, v === true ? "" : v);
  }
  node.append(...children.flat(Infinity).filter(c => c != null && c !== false));
  return node;
}

// One wording for each reason a part of a page has nothing to show.
const EMPTY = {
  "no-days": () => "No day has been processed yet.",
  unavailable: what => `${what ?? "These figures"} can't be shown right now.`,
  "one-day": what => `${what ?? "A day-by-day line"} will appear once there are two days of data.`,
};

export function emptyNote(kind, what) {
  return h("p", {class: "empty"}, (EMPTY[kind] ?? EMPTY.unavailable)(what));
}

// Run a part of a page; if it cannot be drawn, say so in plain words in its
// place and keep the rest of the page.
export function guard(render, what) {
  try {
    const out = render();
    return out ?? emptyNote("unavailable", what);
  } catch (e) {
    console.warn("part of the page could not be drawn:", e);
    return emptyNote("unavailable", what);
  }
}

// A row of figures, each with a short label above and a note below:
// stats([{label, value, note, big}]).
export function stats(items) {
  return h("dl", {class: "stat-row"}, ...items.filter(Boolean).map(s =>
    h("div", {class: s.big ? "stat stat-big" : "stat"},
      h("dt", {class: "label"}, s.label),
      h("dd", {class: "value"}, s.value),
      s.note ? h("dd", {class: "note"}, s.note) : null)));
}

// A switch between the latest days and all of them, for `viewof`. It is only
// shown once there are more days than a chart holds by default.
export function showAllToggle(days) {
  if (days <= RECENT_DAYS) return Object.assign(h("span", {hidden: true}), {value: false});
  const box = h("input", {type: "checkbox"});
  const label = h("label", {class: "toggle"}, box, `Show all ${count(days)} days`);
  Object.defineProperty(label, "value", {get: () => box.checked});
  return label;
}

// " · the figures run through <date>" when the site's figures (as the
// collector last read them from the site, status_now.site_data_through) are
// more than two days old; empty otherwise. Holidays and the nightly job's
// hour make one or two days ordinary.
export function staleFigures(through, now = new Date()) {
  if (!through) return "";
  const today = new Date(now.toLocaleString("en-US", {timeZone: "America/Los_Angeles"}));
  const day = new Date(`${through}T12:00:00`);
  const days = Math.floor((today - day) / 86_400_000);
  return days > 2 ? ` · the figures run through ${longDate(through)}, ${days} days ago` : "";
}

// Keep a live badge up to date. `el` holds .live-state (announced to screen
// readers when it changes) and .live-age (not announced: it changes every
// minute). Checks again each minute while the tab is visible. Returns a
// function that stops it.
export function watchLiveStatus(el, {every = 60_000, read = liveStatus} = {}) {
  const state = el.querySelector(".live-state");
  const ageEl = el.querySelector(".live-age");
  const ago = s => s < 90 ? "in the last minute" : s < 5400 ? `${Math.round(s / 60)} minutes ago` : `${Math.round(s / 3600)} hours ago`;
  const show = (cls, text, age = "") => {
    el.classList.remove("ok", "down", "unknown");
    el.classList.add(cls);
    if (state.textContent !== text) state.textContent = text;
    ageEl.textContent = age;
  };
  let stopped = false, timer = null;
  async function check() {
    if (stopped || document.hidden) return;
    const status = await read();
    if (stopped) return;
    const vp = Array.isArray(status) ? status.find(d => d.feed === "vehiclePositions") : null;
    const age = vp?.last_ok_age_s;
    const behind = staleFigures(vp?.site_data_through);
    if (vp == null) show("unknown", "The collector's status can't be read right now");
    else if (age == null) show("down", "Collector delayed", " · no update in the last 2 days" + behind);
    else if (age < 120) show("ok", "Saving now", ` · last update ${ago(age)}` + behind);
    else show("down", "Collector delayed", ` · last update ${ago(age)}` + behind);
  }
  const onVisible = () => { if (!document.hidden) check(); };
  document.addEventListener("visibilitychange", onVisible);
  timer = setInterval(check, every);
  check();
  return () => {
    stopped = true;
    clearInterval(timer);
    document.removeEventListener("visibilitychange", onVisible);
  };
}

export const SERIES = "var(--series-1)";

// --- period switch --------------------------------------------------------------

// The periods the On time and Data quality pages can show, in order.
export const PERIODS = [["hour", "Hour"], ["day", "Day"], ["week", "Week"], ["month", "Month"]];
const PERIOD_KEY = "oitd-period";

function storedPeriod() {
  try {
    const fromUrl = new URL(location.href).searchParams.get("period");
    if (PERIODS.some(([k]) => k === fromUrl)) return fromUrl;
    const saved = localStorage.getItem(PERIOD_KEY);
    if (PERIODS.some(([k]) => k === saved)) return saved;
  } catch (e) {
    // A private window may refuse storage; the default does.
  }
  return null;
}

function keepPeriod(value) {
  try {
    const url = new URL(location.href);
    url.searchParams.set("period", value);
    history.replaceState(history.state, "", url);
    localStorage.setItem(PERIOD_KEY, value);
  } catch (e) {
    // Nothing to keep it in: the switch still works on this page.
  }
}

// One row of buttons that works as a single choice (a radio group): Hour,
// Day, Week, Month. Arrow keys move between them, as in any radio group.
// The choice is kept in the address (?period=week), so a link opens the same
// view, and in this browser, so the other page opens on it too. Use as
// `viewof period = periodInput()`.
export function periodInput({value = null, label = "Show the figures by"} = {}) {
  const group = h("div", {class: "period-switch", role: "radiogroup", "aria-label": label});
  const buttons = PERIODS.map(([key, text]) => {
    const b = h("button", {type: "button", role: "radio", "data-period": key}, text);
    b.addEventListener("click", () => set(key, true));
    return b;
  });
  group.append(h("span", {class: "period-label", "aria-hidden": "true"}, "Show by"), ...buttons);
  function set(key, chosen) {
    group.value = key;
    for (const b of buttons) {
      const on = b.dataset.period === key;
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    }
    if (chosen) {
      keepPeriod(key);
      group.dispatchEvent(new Event("input", {bubbles: true}));
    }
  }
  group.addEventListener("keydown", e => {
    const i = PERIODS.findIndex(([k]) => k === group.value);
    const step = {ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1}[e.key];
    let next = null;
    if (step) next = (i + step + PERIODS.length) % PERIODS.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = PERIODS.length - 1;
    if (next == null) return;
    e.preventDefault();
    set(PERIODS[next][0], true);
    buttons[next].focus();
  });
  set(value ?? storedPeriod() ?? "day", false);
  return group;
}

// The name of a period for a chart: "the week of November 16, 2026",
// "November 2026", an hour, or a day.
export function periodName(grain, key) {
  if (key == null) return "–";
  if (grain === "hour") return hourLabel(key);
  if (grain === "week") return `Week of ${longDate(key)}`;
  if (grain === "month") {
    const d = new Date(`${key}-01T12:00:00Z`);
    return d.toLocaleDateString("en-US", {month: "long", year: "numeric", timeZone: "UTC"});
  }
  return longDate(key);
}

// One rounding rule for shares, used by stats and chart titles alike: whole
// percents, or one decimal when the share is within 1 point of 0% or 100%
// (where whole percents would hide the difference that matters).
export function fmtShare(x) {
  if (x == null || Number.isNaN(x)) return "–";
  return pct(x, x < 0.01 || x > 0.99 ? 1 : 0);
}

// A small line of daily values, for the home page: drawn without the chart
// library, and hidden from screen readers (the figure beside it says the
// same thing in words).
export function sparkline(values, {width = 120, height = 28} = {}) {
  const v = values.filter(x => x != null);
  if (v.length < 2) return null;
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const step = width / (values.length - 1);
  const pts = values.map((x, i) => (x == null ? null : `${(i * step).toFixed(1)},${(height - 3 - ((x - lo) / span) * (height - 6)).toFixed(1)}`)).filter(Boolean);
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  for (const [k, val] of Object.entries({class: "spark", width, height, viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true", focusable: "false"})) svg.setAttribute(k, val);
  const line = document.createElementNS(NS, "polyline");
  line.setAttribute("points", pts.join(" "));
  svg.append(line);
  return svg;
}

// Early, on time and late as one bar that adds up to 100%, with the three
// shares written out beside it.
export function splitBar(b) {
  const parts = [["early", "early", b?.early_share_of_observed], ["on-time", "on time", b?.on_time_share_of_observed], ["late", "late", b?.late_share_of_observed]];
  if (parts.some(([, , x]) => x == null)) return null;
  const bar = h("div", {class: "split-bar", "aria-hidden": "true"},
    ...parts.map(([cls, , x]) => { const s = h("span", {class: `part ${cls}`}); s.style.width = `${(x * 100).toFixed(2)}%`; return s; }));
  const words = h("p", {class: "split-words"}, parts.map(([, word, x]) => `${fmtShare(x)} ${word}`).join(" · "));
  return h("div", {class: "split"}, bar, words);
}

// A CSV file read without d3, for a page that needs no chart library (the
// Download page). Cells are typed as d3.autoType types them for these files:
// numbers become numbers, true and false become booleans, an empty cell is
// null. Quoted cells may hold commas, quotes ("") and line breaks.
export async function csvPlain(path) {
  try {
    const r = await fetch(path);
    return r.ok ? parseCsv(await r.text()) : [];
  } catch (e) {
    return [];
  }
}

export function parseCsv(text) {
  const rows = [];
  let row = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += c;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  const [header, ...body] = rows.filter(r => r.length > 1 || r[0] !== "");
  if (!header) return [];
  const type = v => v === "" ? null : v === "true" ? true : v === "false" ? false
    : /^-?\d+(\.\d+)?$/.test(v) ? +v : v;
  return body.map(r => Object.fromEntries(header.map((k, i) => [k, type(r[i] ?? "")])));
}
