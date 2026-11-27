// What every site test starts from.
//
// The pages load their chart and map libraries from a CDN, map tiles from
// OpenStreetMap and the collector's status from Supabase. The tests answer
// all three themselves, so they need no network and give the same result
// every time:
//
//   cdn.jsdelivr.net/npm/<name>@<version>/<file>  ->  node_modules/<name>/<file>
//   tile.openstreetmap.org                        ->  one gray pixel
//   the collector's status                        ->  STATUS below
//
// A request to any other outside address fails the test, and so does any
// error the page logs or any cell that fails to run. A page that quietly
// shows a red error box, or starts calling a new server, cannot pass.
import {test as base, expect} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const modules = path.join(here, "node_modules");

export const PAGES = ["index", "routes", "ontime", "quality", "data", "methods", "codebook", "glossary", "limits", "compare"];
export const STATUS = [
  {feed: "vehiclePositions", last_ok_age_s: 7},
  {feed: "tripUpdates", last_ok_age_s: 7},
  {feed: "serviceAlerts", last_ok_age_s: 140},
];

const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGO4c+fOfwAIJgNjcY3aBQAAAABJRU5ErkJggg==",
  "base64",
);
const TYPES = {".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".png": "image/png"};

function cdnFile(url) {
  // /npm/@scope/name@1.2.3/dist/file.js  or  /npm/name@1.2.3
  const m = url.pathname.match(/^\/npm\/((?:@[^/]+\/)?[^/@]+)(?:@([^/]+))?(\/.*)?$/);
  if (!m) return {error: `not a package address: ${url.href}`};
  const [, name, wanted, rest] = m;
  const dir = path.join(modules, name);
  if (!fs.existsSync(dir)) return {error: `${name} is not installed in tests/site (asked for ${url.href})`};
  const pkg = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8"));
  if (wanted && wanted !== pkg.version && !pkg.version.startsWith(`${wanted}.`)) {
    return {error: `page asks for ${name}@${wanted}; tests/site has ${pkg.version}`};
  }
  const entry = rest && rest !== "/" ? rest.slice(1) : pkg.jsdelivr ?? pkg.unpkg ?? pkg.main ?? "index.js";
  const file = path.join(dir, entry);
  if (!fs.existsSync(file)) return {error: `${name} has no file ${entry}`};
  return {file};
}

export const test = base.extend({
  // Problems the page ran into; each test ends by checking the list is empty.
  problems: async ({}, use) => {
    await use([]);
  },
  // Outside addresses the page asked for, by host.
  asked: async ({}, use) => {
    await use([]);
  },
  status: [STATUS, {option: true}],

  page: async ({page, baseURL, problems, asked, status}, use) => {
    const origin = new URL(baseURL).origin;
    await page.route(url => url.origin !== origin, async route => {
      const url = new URL(route.request().url());
      asked.push(url.href);
      if (url.hostname === "cdn.jsdelivr.net") {
        const found = cdnFile(url);
        if (found.error) {
          problems.push(found.error);
          return route.abort();
        }
        return route.fulfill({
          body: fs.readFileSync(found.file),
          contentType: TYPES[path.extname(found.file)] ?? "application/octet-stream",
          headers: {"access-control-allow-origin": "*"},
        });
      }
      if (url.hostname === "tile.openstreetmap.org") {
        return route.fulfill({body: PIXEL, contentType: "image/png"});
      }
      if (url.hostname.endsWith(".supabase.co")) {
        if (status === "down") return route.abort();
        return route.fulfill({
          json: status,
          headers: {"access-control-allow-origin": "*", "access-control-allow-headers": "*"},
        });
      }
      problems.push(`the page asked an unexpected server: ${url.href}`);
      return route.abort();
    });
    page.on("console", msg => {
      if (msg.type() === "error") problems.push(`console: ${msg.text().slice(0, 400)}`);
    });
    page.on("pageerror", err => problems.push(`page error: ${String(err).slice(0, 400)}`));
    await use(page);
  },
});

export {expect};

// Open a page and wait until every cell has run and the page has gone quiet.
export async function open(page, name, {hash = "", query = ""} = {}) {
  await page.goto(`${name}.html${query}${hash}`);
  await settle(page);
}

export async function settle(page) {
  await page.waitForLoadState("networkidle");
  await page.waitForFunction(
    () => document.querySelectorAll(".observablehq--running").length === 0,
    null,
    {timeout: 15_000},
  );
  // Charts measure their own width after layout; give them one frame.
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
}

// Cells that failed to run show an error box in the page.
export async function errorCells(page) {
  return page.locator(".observablehq--error, .ojs-error, .callout-important.ojs-error").allInnerTexts();
}

export async function expectClean(page, problems) {
  expect(await errorCells(page), "cells that failed to run").toEqual([]);
  expect(problems, "problems the page ran into").toEqual([]);
}
