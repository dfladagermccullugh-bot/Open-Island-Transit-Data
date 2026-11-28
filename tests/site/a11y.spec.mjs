// Every page is scanned with axe against WCAG 2.2 level AA, in the light and
// dark themes, at a desk and on a phone, with every folded section open so
// nothing is skipped. A scan finds what a machine can find (contrast, names,
// roles, structure); behavior.spec.mjs covers what has to be tried by hand.
import fs from "node:fs";
import {createRequire} from "node:module";
import {PAGES, expect, open, test} from "./fixtures.mjs";

const axeSource = fs.readFileSync(createRequire(import.meta.url).resolve("axe-core/axe.min.js"), "utf8");
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];
const VIEWS = [...PAGES.map(name => [name, ""]), ["routes", "#60"]];

// Known findings that are accepted, each with its reason. Empty on purpose:
// anything added here should come with a comment and a plan to remove it.
const ACCEPTED = new Set([]);

async function scan(page) {
  for (const d of await page.locator("details").all()) await d.evaluate(n => (n.open = true));
  // Charts inside a section draw when it opens.
  await page.waitForTimeout(300);
  await page.addScriptTag({content: axeSource});
  const results = await page.evaluate(async tags => {
    // One exception, written down here so it is not forgotten. The stop
    // markers of a picked route sit where the stops are, and stops a block
    // apart overlap on the map, so a marker can be smaller than 24 by 24
    // pixels where a neighbor covers part of it. WCAG 2.5.8 allows this when
    // the same thing can be done with another control on the page: every
    // stop and its figures are in the chart and the table under the map.
    // (The buses on the replay page are drawn side by side where they meet,
    // so they need no such exception.)
    window.axe.configure({rules: [{id: "target-size", selector: "*:not(.stop-marker)"}]});
    const r = await window.axe.run(document, {runOnly: {type: "tag", values: tags}});
    return r.violations.map(v => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      nodes: v.nodes.slice(0, 4).map(n => ({target: n.target.join(" "), summary: (n.failureSummary ?? "").replace(/\s+/g, " ").slice(0, 260)})),
      count: v.nodes.length,
    }));
  }, TAGS);
  return results.filter(v => !ACCEPTED.has(v.id));
}

for (const [name, hash] of VIEWS) {
  test(`${name}${hash} has no accessibility violations`, async ({page}) => {
    await open(page, name, {hash});
    expect(await scan(page)).toEqual([]);
  });
}

// The navbar's menu, open: its links are only drawn when it is.
test("the open Data & methods menu has no accessibility violations", async ({page}, info) => {
  await open(page, "quality");
  if (info.project.name.startsWith("phone")) {
    await page.locator(".navbar-toggler").tap();
    // Bootstrap adds "show" once the navbar has finished unfolding.
    await expect(page.locator("#navbarCollapse")).toHaveClass(/\bshow\b/);
  }
  const toggle = page.getByRole("button", {name: "Data & methods"});
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  // Let the navbar finish unfolding and its links finish changing color.
  await page.waitForTimeout(500);
  expect(await scan(page)).toEqual([]);
});
