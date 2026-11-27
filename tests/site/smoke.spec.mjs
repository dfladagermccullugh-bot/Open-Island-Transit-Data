// Every page loads, runs all its cells, logs no error and shows its figures,
// whatever data stands behind it.
import {PAGES, expect, expectClean, open, test} from "./fixtures.mjs";
import {SCENARIOS, useScenario} from "./scenarios.mjs";

const DATA_PAGES = ["index", "routes", "ontime", "quality", "data", "limits"];

for (const name of PAGES) {
  test(`${name} runs clean`, async ({page, problems}) => {
    await open(page, name);
    await expectClean(page, problems);
    await expect(page.locator("main h1")).toHaveCount(1);
  });
}

for (const scenario of Object.keys(SCENARIOS).filter(s => s !== "real")) {
  for (const name of DATA_PAGES) {
    test(`${name} runs clean with ${scenario} data`, async ({page, problems}) => {
      await useScenario(page, scenario);
      await open(page, name);
      // A missing file is a 404 the page is built to expect, not a problem.
      const real = problems.filter(p => !/Failed to load resource.*404/.test(p));
      await expectClean(page, real);
      expect(await page.locator("main").innerText()).not.toMatch(/NaN|undefined|Invalid Date|\[object Object\]/);
    });
  }
}

test("a route opened by its address runs clean", async ({page, problems}) => {
  await open(page, "routes", {hash: "#60"});
  await expectClean(page, problems);
  await expect(page.locator("main h2").first()).toContainText("Route 60");
});

test("a missing page shows the 404 page with working styles and links", async ({page, problems}) => {
  const response = await page.goto("no/such/page.html");
  expect(response.status()).toBe(404);
  await expect(page.locator("main")).toContainText(/find|exist|missing/i);
  // Styles and links must not depend on how deep the missing address was.
  await expect(page.locator(".navbar")).toHaveCSS("display", "flex");
  const home = await page.locator(".navbar a.navbar-brand").getAttribute("href");
  expect(new URL(home, page.url()).pathname).toMatch(/^\/Open-Island-Transit-Data\/(index\.html)?$/);
  // The footer's date is read from the site's own folder, not from beside
  // the missing address: the only 404 is the page that was asked for.
  await expect(page.locator("#data-through")).toContainText("Data through");
  expect(problems.filter(p => /404/.test(p))).toHaveLength(1);
  expect(problems.filter(p => !/404/.test(p))).toEqual([]);
});
