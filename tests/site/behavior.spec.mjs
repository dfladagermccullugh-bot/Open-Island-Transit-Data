// What the pages do when a reader uses them: picking routes, resizing,
// using only the keyboard, tapping on a phone, and reading days that need
// special handling.
import {expect, expectClean, open, settle, test} from "./fixtures.mjs";
import {useScenario} from "./scenarios.mjs";

const desktopOnly = ({}, info) => test.skip(!info.project.name.startsWith("desktop"), "a desk-sized window");
const phoneOnly = ({}, info) => test.skip(!info.project.name.startsWith("phone"), "a touch screen");

test.describe("picking a route", () => {
  test("the picker, the address, the caption and the detail move together", async ({page, problems}) => {
    await open(page, "routes");
    await expect(page.locator("#map-caption")).toContainText("All routes are shown");
    await page.locator("#route-select").selectOption({label: "Route 60 · Freeland - Langley - Clinton"});
    await expect(page).toHaveURL(/routes\.html#60$/);
    await expect(page.locator("#map-caption")).toContainText("Route 60");
    await expect(page.locator("#route-detail h2")).toContainText("Route 60");
    // The Back button returns to all routes, and Forward to the route.
    await page.goBack();
    await expect(page.locator("#route-select")).toHaveValue("");
    await expect(page.locator("#route-detail h2")).toContainText("All routes");
    await page.goForward();
    await expect(page.locator("#route-detail h2")).toContainText("Route 60");
    await expectClean(page, problems);
  });

  test("a route's name in the table of all routes opens that route", async ({page}) => {
    await open(page, "routes");
    await page.locator("#route-detail table a", {hasText: "Route 1C"}).click();
    await expect(page.locator("#route-select")).not.toHaveValue("");
    await expect(page.locator("#route-detail h2")).toContainText("Route 1C");
  });

  test("an address that names no route shows all routes and is tidied", async ({page}) => {
    await open(page, "routes", {hash: "#no-such-route"});
    await expect(page.locator("#route-detail h2")).toContainText("All routes");
    await expect(page).toHaveURL(/routes\.html$/);
  });

  test("the picked route's stops can be reached and read with the keyboard", async ({page}) => {
    await open(page, "routes", {hash: "#60"});
    const stop = page.locator(".stop-marker").first();
    await expect(stop).toHaveAttribute("aria-label", /on time|too few/);
    await stop.focus();
    await expect(page.locator(".leaflet-tooltip.map-tip")).toBeVisible();
  });
});

test.describe("resizing and turning the screen", () => {
  test.beforeEach(desktopOnly);

  test("keeps the picked route, the same map, the reader's zoom and open sections", async ({page, problems}) => {
    await open(page, "routes", {hash: "#60"});
    await page.locator(".route-map").evaluate(n => (n.dataset.mark = "same-map"));
    await page.locator(".leaflet-control-zoom-in").click();
    await page.waitForTimeout(400);
    // Leaflet keeps its zoom as the scale of this element.
    const zoom = () => page.locator(".route-map .leaflet-proxy").evaluate(n => n.style.transform.match(/scale\(([^)]+)\)/)[1]);
    const before = await zoom();
    await page.locator("details.as-table").first().evaluate(n => (n.open = true));

    for (const width of [800, 390, 1280]) {
      await page.setViewportSize({width, height: 900});
      await settle(page);
      await page.waitForTimeout(250);
      await expect(page.locator("#route-select")).toHaveValue(/.+/);
      await expect(page.locator("#route-detail h2")).toContainText("Route 60");
      await expect(page.locator(".route-map")).toHaveAttribute("data-mark", "same-map");
      await expect(page.locator(".route-map")).toHaveCount(1);
    }
    expect(await zoom()).toBe(before);
    expect(await page.locator("details.as-table").first().evaluate(n => n.open)).toBe(true);
    await expectClean(page, problems);
  });

  test("charts redraw to fit and never spill past the page", async ({page}) => {
    await open(page, "quality");
    for (const width of [1280, 700, 360]) {
      await page.setViewportSize({width, height: 900});
      await settle(page);
      await page.waitForTimeout(250);
      const spill = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(spill, `sideways scroll at ${width}px`).toBeLessThanOrEqual(1);
    }
  });
});

test.describe("the home page map", () => {
  test("each route is a link under the map, named with its figure", async ({page}) => {
    await open(page, "index");
    const link = page.locator(".route-links a", {hasText: /^60$/});
    await expect(link).toHaveAttribute("aria-label", /Route 60: .*(on time|too few)/i);
    await link.click();
    await expect(page).toHaveURL(/routes\.html#60$/);
  });

  test("a tap on a route opens a card with a link and stays on the page", async ({page}, info) => {
    phoneOnly({}, info);
    await open(page, "index");
    await page.locator(".route-map").scrollIntoViewIfNeeded();
    const line = page.locator(".route-hit").last();
    // Tap a point that is on the line itself, halfway along it.
    const point = await line.evaluate(n => {
      const p = n.getPointAtLength(n.getTotalLength() / 2);
      const m = n.getScreenCTM();
      return {x: p.x * m.a + m.e, y: p.y * m.d + m.f};
    });
    await page.touchscreen.tap(point.x, point.y);
    await expect(page.locator(".map-card .leaflet-popup-content a")).toBeVisible();
    await expect(page).toHaveURL(/index\.html$/);
  });
});

test.describe("terms that explain themselves", () => {
  test("open on focus, close on Escape, and are described to screen readers", async ({page}) => {
    await open(page, "quality");
    const term = page.locator(".term", {hasText: "nonresponse"});
    await expect(term).toHaveAttribute("role", "button");
    const describedBy = await term.getAttribute("aria-describedby");
    await expect(page.locator(`#${describedBy}`)).toContainText("should be there but is missing");
    await term.focus();
    const tip = page.locator(".term-tip");
    await expect(tip).toBeVisible();
    await expect(tip).toContainText("should be there but is missing");
    await page.keyboard.press("Escape");
    await expect(tip).toBeHidden();
    await expect(term).toBeFocused();
    // The box never hangs off the edge of the screen.
    await page.keyboard.press("Enter");
    await expect(tip).toBeVisible();
    const box = await tip.boundingBox();
    const width = page.viewportSize().width;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
  });
});

test.describe("tables", () => {
  test("sort from the keyboard and say how they are sorted", async ({page}) => {
    await open(page, "routes");
    const header = page.locator("#route-detail th", {hasText: "On time"});
    await expect(header).toHaveAttribute("aria-sort", "none");
    await header.locator("button").focus();
    await page.keyboard.press("Enter");
    await expect(header).toHaveAttribute("aria-sort", "descending");
    const shares = await page.locator("#route-detail tbody tr td:nth-child(3)").allInnerTexts();
    const numbers = shares.filter(t => t !== "–").map(t => parseFloat(t));
    expect(numbers).toEqual([...numbers].sort((a, b) => b - a));
    // Missing values stay at the bottom whichever way the column runs.
    await page.keyboard.press("Enter");
    await expect(header).toHaveAttribute("aria-sort", "ascending");
    const again = await page.locator("#route-detail tbody tr td:nth-child(3)").allInnerTexts();
    expect(again.indexOf("–") === -1 || again.lastIndexOf("–") === again.length - 1).toBe(true);
  });

  test("the band column sorts by band, best first", async ({page}) => {
    await open(page, "routes");
    const header = page.locator("#route-detail th", {hasText: "Band"});
    await header.locator("button").click();
    await header.locator("button").click();
    await expect(header).toHaveAttribute("aria-sort", /ending/);
    const bands = await page.locator("#route-detail tbody tr td:last-child").allInnerTexts();
    const rank = t => (/80%/.test(t) ? 3 : /60 to/.test(t) ? 2 : /Under/.test(t) ? 1 : 0);
    const ranks = bands.map(rank);
    const sorted = [...ranks].sort((a, b) => a - b);
    expect(ranks.join() === sorted.join() || ranks.join() === sorted.reverse().join()).toBe(true);
  });
});

test.describe("days that need special handling", () => {
  for (const scenario of ["holiday-False", "holiday-false"]) {
    test(`a holiday is listed as a day without service (${scenario})`, async ({page}) => {
      await useScenario(page, scenario);
      await open(page, "quality");
      const row = page.locator("details.more", {hasText: "All days"}).locator("tbody tr", {hasText: "November 26, 2026"});
      await page.locator("details.more > summary", {hasText: "All days"}).click();
      await expect(row).toContainText("No service (holiday)");
      // The day's grades at the top stay those of the last day with service.
      await expect(page.locator("main")).toContainText("These grades are for November 20, 2026");
    });
  }

  test("a missing figure is shown as missing, never as 100%", async ({page}) => {
    await useScenario(page, "long");
    await page.route("**/data/quality/daily.csv", async route => {
      const response = await route.fetch();
      const text = await response.text();
      const lines = text.trim().split("\n");
      const columns = lines[0].split(",");
      const cells = lines.at(-1).split(",");
      cells[columns.indexOf("off_shape_share")] = "";
      await route.fulfill({body: [...lines.slice(0, -1), cells.join(",")].join("\n")});
    });
    await open(page, "quality");
    const stat = page.locator(".stat", {hasText: "Are buses where the route says?"});
    await expect(stat.locator(".value")).toHaveText("–");
  });

  test("with one day of data the pages say a line needs a second day", async ({page, problems}) => {
    await useScenario(page, "one-day");
    await open(page, "ontime");
    await expect(page.locator("main")).toContainText("once a second day comes in");
    // The built site holds only the latest day's detail file, so the detail
    // of this made-up first day is missing; the page is built to expect that.
    await expectClean(page, problems.filter(p => !/Failed to load resource.*404/.test(p)));
  });

  test("a long history shows the latest 90 days and offers the rest", async ({page}) => {
    await useScenario(page, "long");
    await open(page, "ontime");
    const toggle = page.getByLabel("Show all 120 days");
    await expect(toggle).toBeVisible();
    await toggle.check();
    await settle(page);
    await page.locator("details.as-table > summary").first().click();
    await expect(page.locator("details.as-table").first().locator(".show-more")).toContainText("120");
  });

  test("a schedule change is explained where the figures start over", async ({page}) => {
    await useScenario(page, "schedule-change");
    await open(page, "index");
    await expect(page.locator(".headline")).toContainText("changed its schedule on December 27, 2026");
  });
});

test.describe("the collector badge", () => {
  test("says the collector is saving, in a region screen readers announce", async ({page}) => {
    await open(page, "index");
    const state = page.locator("#live .live-state");
    await expect(state).toHaveText("Saving now");
    await expect(state).toHaveAttribute("role", "status");
    await expect(page.locator("#live .live-age")).toContainText("last update in the last minute");
  });

  test.describe("when the status cannot be read", () => {
    test.use({status: "down"});
    test("says so plainly and logs nothing but the failed request", async ({page, problems}) => {
      await open(page, "index");
      await expect(page.locator("#live .live-state")).toHaveText("The collector's status can't be read right now");
      expect(problems.filter(p => !/Failed to load resource/.test(p))).toEqual([]);
    });
  });

  test.describe("when the collector is behind", () => {
    test.use({status: [{feed: "vehiclePositions", last_ok_age_s: 3000}]});
    test("says how long ago the last update was", async ({page}) => {
      await open(page, "index");
      await expect(page.locator("#live .live-state")).toHaveText("Collector delayed");
      await expect(page.locator("#live .live-age")).toContainText("50 minutes ago");
    });
  });

  test.describe("when the site's figures have fallen behind", () => {
    test.use({status: [{feed: "vehiclePositions", last_ok_age_s: 7, site_data_through: "2026-01-02"}]});
    test("says which day the figures run through", async ({page}) => {
      await open(page, "index");
      await expect(page.locator("#live .live-state")).toHaveText("Saving now");
      await expect(page.locator("#live .live-age")).toContainText("the figures run through January 2, 2026");
    });
  });
});

test.describe("loading", () => {
  test("the map library comes from the site itself, minified, and tiles load once", async ({page, asked}) => {
    await open(page, "index");
    expect(asked.filter(u => /leaflet/.test(u))).toEqual([]);
    const tiles = asked.filter(u => u.includes("tile.openstreetmap.org"));
    expect(tiles.length).toBeGreaterThan(0);
    expect(new Set(tiles).size).toBe(tiles.length);
    const zooms = new Set(tiles.map(u => new URL(u).pathname.split("/")[1]));
    expect([...zooms]).toHaveLength(1);
  });

  for (const name of ["index", "routes"]) {
    test(`a tile server that never answers does not hold up ${name}`, async ({page}) => {
      // The tiles are another server's. If it is slow or silent the map has
      // no background, but the page still finishes loading and still works.
      let asked = 0;
      await page.route("https://tile.openstreetmap.org/**", () => { asked++; });
      const before = asked;
      await page.goto(`${name}.html`, {waitUntil: "load", timeout: 15_000});
      expect(before).toBe(0);
      await expect(page.locator(".route-map .route-line").first()).toBeAttached();
      await expect(page.locator(".route-links a, #route-select").first()).toBeVisible();
      // Tiles are asked for, but only after the page has loaded.
      await expect.poll(() => asked).toBeGreaterThan(0);
    });
  }

  for (const name of ["index", "routes"]) {
    test(`without stylesheets the map on ${name} keeps its size and fetches no tiles`, async ({page, asked}) => {
      // Page readers often skip stylesheets. Leaflet's stylesheet is what
      // keeps the tiles inside the map's box; without it, and without a
      // height set on the box itself, each tile made the box taller and a
      // taller map asked for more tiles, thousands of them.
      await page.route(/\.css(\?|$)/, route => route.fulfill({body: "", contentType: "text/css"}));
      await page.goto(`${name}.html`, {waitUntil: "load", timeout: 15_000});
      const map = page.locator(".route-map");
      await expect(map.locator(".route-line").first()).toBeAttached();
      await expect(page.locator("h1")).toBeVisible();
      await page.waitForTimeout(1500);
      expect(await map.evaluate(n => n.clientHeight)).toBeLessThanOrEqual(640);
      expect(asked.filter(u => u.includes("tile.openstreetmap.org"))).toHaveLength(0);
    });
  }

  test("the page does not jump while its figures arrive", async ({page}) => {
    await page.addInitScript(() => {
      window.__shift = 0;
      new PerformanceObserver(list => {
        for (const e of list.getEntries()) if (!e.hadRecentInput) window.__shift += e.value;
      }).observe({type: "layout-shift", buffered: true});
    });
    for (const name of ["index", "routes", "quality"]) {
      await open(page, name);
      const shift = await page.evaluate(() => window.__shift);
      expect(shift, `layout shift on ${name}`).toBeLessThan(0.1);
    }
  });
});

test.describe("nothing invisible takes the keyboard focus", () => {
  for (const name of ["index", "routes", "ontime", "quality", "data"]) {
    test(`every control on ${name} that can be focused can be seen`, async ({page}) => {
      await open(page, name);
      for (const d of await page.locator("details").all()) await d.evaluate(n => (n.open = true));
      const tiny = await page.evaluate(() =>
        [...document.querySelectorAll("a[href], button, input, select, summary, [tabindex]:not([tabindex='-1'])")]
          // Only what the Tab key stops on: Quarto's code lines carry empty
          // anchors with tabindex -1, which it does not.
          .filter(n => n.checkVisibility() && n.tabIndex >= 0 && !n.closest(".visually-hidden-focusable"))
          .map(n => [n.tagName, n.className?.baseVal ?? n.className, n.getBoundingClientRect()])
          .filter(([, , b]) => b.width < 8 || b.height < 8)
          .map(([tag, cls, b]) => `${tag}.${cls} ${Math.round(b.width)}x${Math.round(b.height)}`));
      expect(tiny).toEqual([]);
    });
  }
});

test.describe("the download page", () => {
  test("links go straight to the files, not to pages about them", async ({page}) => {
    await open(page, "data");
    const daily = page.locator(".download-list a", {hasText: "quality/daily.csv"});
    await expect(daily).toHaveAttribute("href", /^https:\/\/raw\.githubusercontent\.com\/.+\/data\/quality\/daily\.csv$/);
  });

  test("lists every table with what one row is", async ({page}) => {
    await open(page, "data");
    const table = page.getByRole("table", {name: "Every table in the archive"});
    await expect(table.locator("tbody tr")).toHaveCount(12);
    await expect(table.locator("tbody tr", {hasText: "Stop events"})).toContainText("one scheduled stop of one scheduled trip");
  });

  test("shows the files of the day that is picked, with a link and a hash for each", async ({page}) => {
    await open(page, "data");
    await expect(page.locator("#file-day")).toHaveValue("2026-11-20");
    const table = page.getByRole("table", {name: "Files for November 20, 2026"});
    const row = table.locator("tbody tr", {hasText: "stop-events-2026-11-20.parquet"});
    await expect(row.locator("a")).toHaveAttribute("href", /releases\/download\/parsed-2026-11\/stop-events-2026-11-20\.parquet$/);
    await expect(row.locator(".hash span").first()).toHaveAttribute("title", /^[0-9a-f]{64}$/);
    await page.locator("#file-day").selectOption("2026-11-19");
    await expect(page.getByRole("table", {name: "Files for November 19, 2026"})).toBeVisible();
    await expect(page.locator("main")).toContainText("static-2026-11-19-trillium-2.zip");
  });

  test("each example has a button that copies it", async ({page, context}, info) => {
    test.skip(!info.project.name.startsWith("desktop"), "clipboard access is granted on the desk browser");
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await open(page, "data");
    await page.locator("details.more > summary", {hasText: "Python"}).click();
    const button = page.locator("details.more", {hasText: "Python"}).getByRole("button", {name: "Copy this code"});
    await button.click();
    await expect(button).toHaveText("Copied");
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toContain("import pandas as pd");
    expect(copied).toContain("files.csv");
  });

  test("still reads when the index and the dictionary are not published yet", async ({page, problems}) => {
    await page.route("**/data/files.csv", route => route.fulfill({status: 404, body: ""}));
    await page.route("**/data/datapackage.json", route => route.fulfill({status: 404, body: ""}));
    await open(page, "data");
    await expect(page.locator("main")).toContainText("The list of tables can't be shown right now.");
    await expect(page.locator("main")).toContainText("The list of files can't be shown right now.");
    await expect(page.locator("#file-day")).toBeHidden();
    await expectClean(page, problems.filter(p => !/Failed to load resource.*404/.test(p)));
  });
});

test.describe("the period switch", () => {
  test("arrow keys move the choice, the chart follows, and the address keeps it", async ({page}) => {
    await open(page, "ontime");
    const group = page.getByRole("radiogroup", {name: "Show the figures by"});
    await expect(group.getByRole("radio", {name: "Day"})).toHaveAttribute("aria-checked", "true");
    await group.getByRole("radio", {name: "Day"}).focus();
    await page.keyboard.press("ArrowRight");
    await settle(page);
    await expect(group.getByRole("radio", {name: "Week"})).toHaveAttribute("aria-checked", "true");
    await expect(group.getByRole("radio", {name: "Week"})).toBeFocused();
    expect(new URL(page.url()).searchParams.get("period")).toBe("week");
    await expect(page.locator("main")).toContainText("Week of November 16, 2026");
    await page.keyboard.press("Home");
    await settle(page);
    await expect(page.locator("main")).toContainText("grouped by the hour");
  });

  test("a link with a period opens on it", async ({page}) => {
    await open(page, "ontime", {query: "?period=month"});
    await expect(page.getByRole("radio", {name: "Month"})).toHaveAttribute("aria-checked", "true");
    await expect(page.locator("main")).toContainText("November 2026");
  });
});

test.describe("the home page headline", () => {
  test("pools the latest service days and says when it can first compare", async ({page}) => {
    await open(page, "index");
    const head = page.locator(".slot-headline");
    await expect(head).toContainText("over the last 2 days with service, to November 20, 2026");
    await expect(head).toContainText("Not enough days yet to compare");
    await expect(head.locator(".split-words")).toContainText("on time");
    await expect(head.locator(".trust")).toContainText("found nothing wrong");
    await expect(head.locator("a", {hasText: "How we measured this"})).toHaveAttribute("href", "methods.html#on-time-performance");
  });
});

test.describe("weight", () => {
  // The JavaScript a page loads, compressed as a server sends it, stays
  // within 450 KB (the plan of November 27, 2026; Lighthouse checks the same
  // budget in CI). Quarto's Observable runtime alone is 212 KB, so the pages
  // load a trimmed chart library (site/vendor/plot-0.6.11, about 95 KB)
  // instead of all of Plot and d3 (154 KB). The Download page needs none.
  const BUDGET_KB = 450;
  for (const name of ["index", "routes", "replay", "stops", "ontime", "quality", "compare", "data"]) {
    test(`${name} loads at most ${BUDGET_KB} KB of JavaScript`, async ({page}) => {
      const {gzipSync} = await import("node:zlib");
      const scripts = [], fonts = [];
      page.on("request", r => { if (r.resourceType() === "font") fonts.push(r.url()); });
      page.on("response", r => {
        if (/javascript/.test(r.headers()["content-type"] ?? "") || /\.m?js(\?|$)/.test(r.url())) scripts.push(r);
      });
      await open(page, name);
      let total = 0;
      const sizes = [];
      for (const r of scripts) {
        const body = await r.body().catch(() => Buffer.alloc(0));
        const kb = gzipSync(body).length / 1024;
        total += kb;
        sizes.push(`${r.url().split("/").slice(-2).join("/")} ${kb.toFixed(0)} KB`);
      }
      console.log(`${name}: ${total.toFixed(0)} KB of JavaScript (gzipped): ${sizes.join(", ")}`);
      expect(total, sizes.join("\n")).toBeLessThanOrEqual(BUDGET_KB);
      if (name === "data") expect(scripts.some(r => /plot|\/d3@/.test(r.url()))).toBe(false);
      // Never the full libraries from the CDN.
      expect(scripts.filter(r => /\/d3@|plot\.umd/.test(r.url())).map(r => r.url())).toEqual([]);
      // No icon font: the one icon on the page is drawn inline.
      expect(fonts.filter(u => /bootstrap-icons/.test(u))).toEqual([]);
    });
  }
});

test.describe("what this data can and can't tell you", () => {
  test("fills every figure from the published files and lists the corrections", async ({page}) => {
    await open(page, "limits");
    const main = page.locator("main");
    await expect(main).toContainText("we put a time on 85.3% of the departures");
    await expect(main).toContainText("ran two or more routes");
    // The visible text: the hidden source of each cell also holds the words.
    expect(await main.innerText()).not.toContain("not yet known");
    await expect(page.locator(".corrections table")).toContainText("events/0.5.0");
  });

  test("is linked from the home page headline", async ({page}) => {
    await open(page, "index");
    await expect(page.locator(".slot-headline a", {hasText: "What these figures can't tell you"})).toHaveAttribute("href", "limits.html");
  });
});

test.describe("stops and trips", () => {
  test("a stop opened by its address shows its departures and one trip stop by stop", async ({page}) => {
    await open(page, "stops", {hash: "#178409"});
    await expect(page.locator("#stop-pick")).toHaveValue("178409");
    await expect(page.locator("main")).toContainText("Clinton Ferry Terminal");
    await expect(page.locator("main")).toContainText("departures we could time were on time");
    const trip = page.locator("table", {has: page.locator("caption", {hasText: /^Trip \d+ on /})});
    await expect(trip).toBeVisible();
    await expect(trip).toContainText("(timepoint)");
  });
});

test.describe("replay a day", () => {
  test("moving the clock shows the buses out at that time, and Play moves it on", async ({page}) => {
    await open(page, "replay", {hash: "#2026-11-20"});
    await expect(page.locator("#day-pick")).toHaveValue("2026-11-20");
    const clock = page.locator("#replay-clock");
    await clock.evaluate(el => { el.value = 32400; el.dispatchEvent(new Event("input", {bubbles: true})); });
    await expect(page.locator(".replay-time")).toHaveText("9:00 AM");
    await expect(page.locator(".replay-summary")).toContainText(/At 9:00 AM on November 20, 2026, we saw \d+ buses: \d+ on time/);
    await expect(page.locator(".bus-marker").first()).toBeAttached();
    expect(await page.locator(".bus-marker").count()).toBeGreaterThan(3);
    const table = page.locator("table", {has: page.locator("caption", {hasText: "Buses at 9:00 AM"})});
    await expect(table).toBeVisible();
    await expect(table.locator("tbody tr").first()).toContainText(/Route/);
    await page.locator(".replay-play").click();
    await expect(page.locator(".replay-play")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator(".replay-time")).not.toHaveText("9:00 AM");
    await page.locator(".replay-play").click();
    await expect(page.locator(".replay-play")).toHaveText("Play");
  });
});

test.describe("statistics", () => {
  test("the headline gives its range, and predictions are drawn as 20 dots", async ({page}) => {
    await open(page, "index");
    await expect(page.locator(".headline")).toContainText("very likely between 65% and 71%");
    await open(page, "quality");
    await page.locator("summary", {hasText: "Arrival predictions"}).click();
    const fig = page.locator("figure", {hasText: "Arrival predictions made 5 minutes ahead"});
    await expect(fig).toBeVisible();
    await expect(fig).toContainText(/Of 20 buses the feed said would arrive in 5 minutes, about \d+ came within a minute/);
    expect(await fig.locator("svg circle").count()).toBe(20);
  });
});

test("a peer sampled with our own method shows its share and range (class A)", async ({page}) => {
  await open(page, "compare");
  const row = page.locator("tr", {hasText: "Halifax Transit"});
  await expect(row).toContainText("77% (74% to 80%), November 24, 2026");
  await expect(row).toContainText("R, B, A");
});

test.describe("the footer", () => {
  test("says how many checks of the publishing gate the figures passed, and when", async ({page}) => {
    await open(page, "index");
    const gate = page.locator("#data-through a");
    await expect(gate).toHaveText("Every figure here passed 13 automatic checks on November 21, 2026");
    await gate.click();
    await expect(page).toHaveURL(/limits\.html#gate$/);
    await expect(page.locator("#gate")).toBeVisible();
  });
});
