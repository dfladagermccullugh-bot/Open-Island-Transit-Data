// The site, shown the same pages with different data behind them.
//
// The pages are rendered once, with the real published files. A scenario
// stands between a page and its data and changes what the page receives, so
// one render can be checked against a holiday, a first day, a long history,
// missing values and missing files.
import {csvFormat, csvParse} from "d3-dsv";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const dataDir = path.resolve(here, process.env.SITE_DIR ?? "../../site/_site", "data");

const isDaily = rel => rel === "ontime/daily.csv" || rel === "quality/daily.csv";
const isJson = rel => rel.endsWith(".json");
const day = (iso, plus) => {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + plus);
  return d.toISOString().slice(0, 10);
};

function editCsv(text, edit) {
  const rows = csvParse(text);
  const out = edit(rows.map(r => ({...r})), rows.columns);
  return csvFormat(out, rows.columns);
}

// A day on which no bus was scheduled: every count zero, every share blank.
function holidayRow(last, columns, date, flag) {
  const row = {};
  for (const c of columns) {
    if (c === "service_date") row[c] = date;
    else if (c === "service_expected") row[c] = flag;
    else if (/version|reference|basis/.test(c)) row[c] = last[c];
    else if (/^(scheduled|observed|on_time|early|late|scheduled_trips|vehicles_seen|canceled_trips)$/.test(c)) row[c] = "0";
    else row[c] = "";
  }
  return row;
}

// Every number that sits two or more levels deep becomes null.
function nullLeaves(value, depth = 0) {
  if (Array.isArray(value)) return value.map(v => nullLeaves(v, depth + 1));
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, nullLeaves(v, depth + 1)]));
  }
  return typeof value === "number" && depth >= 2 ? null : value;
}

// Every object below the top level loses its contents.
function hollow(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([k, v]) => [k, v && typeof v === "object" ? (Array.isArray(v) ? [] : {}) : v]),
  );
}

const holiday = flag => (rel, text) => {
  if (isDaily(rel)) {
    return editCsv(text, (rows, columns) => [...rows, holidayRow(rows.at(-1), columns, "2026-11-26", flag)]);
  }
  if (rel === "ontime/recent.json") {
    return JSON.stringify({...JSON.parse(text), days_without_service: ["2026-11-26"]});
  }
  return text;
};

export const SCENARIOS = {
  // The published files as they are.
  real: (rel, text) => text,

  // Thanksgiving in the daily tables, written the way Python's csv module
  // writes it today ("False") and the way the tables will write it ("false").
  "holiday-False": holiday("False"),
  "holiday-false": holiday("false"),

  // The first morning: one service day.
  "one-day": (rel, text) => {
    if (isDaily(rel)) return editCsv(text, rows => rows.slice(0, 1));
    if (rel === "ontime/recent.json") {
      const r = JSON.parse(text);
      for (const route of Object.values(r.routes)) route.daily = (route.daily ?? []).slice(0, 1);
      return JSON.stringify({...r, days: 1, last_service_date: r.first_service_date});
    }
    return text;
  },

  // Four months of history that run across New Year.
  long: (rel, text) => {
    const n = 120, end = "2027-03-20";
    const wobble = i => 0.9 + 0.2 * (((i * 37) % 17) / 17);
    if (isDaily(rel)) {
      return editCsv(text, rows => Array.from({length: n}, (_, i) => {
        const row = {...rows.at(-1), service_date: day(end, i - n + 1)};
        for (const [k, v] of Object.entries(row)) {
          if (/share|coverage|uptime|rate/.test(k) && v !== "" && !Number.isNaN(+v)) {
            row[k] = String(Math.min(1, +v * wobble(i)).toFixed(4));
          }
        }
        return row;
      }));
    }
    if (rel === "ontime/recent.json") {
      const r = JSON.parse(text);
      for (const route of Object.values(r.routes)) {
        const last = (route.daily ?? []).at(-1);
        if (last) {
          route.daily = Array.from({length: 28}, (_, i) => ({
            ...last,
            service_date: day(end, i - 27),
            on_time_share_of_observed: Math.min(1, last.on_time_share_of_observed * wobble(i)),
          }));
        }
      }
      return JSON.stringify({...r, days: 28, first_service_date: day(end, -27), last_service_date: end});
    }
    return text;
  },

  // A new printed timetable took effect, so the route tables start over.
  "schedule-change": (rel, text) => {
    if (rel !== "ontime/recent.json") return text;
    const r = JSON.parse(text);
    return JSON.stringify({
      ...r,
      timetable_reference: "2026-12-27",
      schedule_change: {effective: "2026-12-27", previous_reference: "2026-09-27"},
    });
  },

  // Files present, numbers missing.
  nulls: (rel, text) => (isJson(rel) && !rel.startsWith("maps/") ? JSON.stringify(nullLeaves(JSON.parse(text))) : text),

  // Files present, whole sections missing.
  hollow: (rel, text) => (isJson(rel) && !rel.startsWith("maps/") && rel !== "build.json" ? JSON.stringify(hollow(JSON.parse(text))) : text),

  // Nothing published yet: every data file is missing.
  empty: () => null,
};

// Stand between the page and its data. Call before opening the page.
export async function useScenario(page, name) {
  const change = SCENARIOS[name];
  if (!change) throw new Error(`no scenario named ${name}`);
  await page.route("**/data/**", async route => {
    const url = new URL(route.request().url());
    const rel = decodeURIComponent(url.pathname.split("/data/")[1] ?? "");
    const file = path.join(dataDir, rel);
    const text = fs.existsSync(file) ? change(rel, fs.readFileSync(file, "utf8")) : null;
    if (text == null) return route.fulfill({status: 404, body: "not found", contentType: "text/plain"});
    return route.fulfill({body: text, contentType: rel.endsWith(".csv") ? "text/csv" : "application/json"});
  });
}
