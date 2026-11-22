// Takes a picture of every page, for a person to look over. Not a comparison:
// it passes as long as the pages load. Run with SHOTS=<folder>.
import {open, test} from "./fixtures.mjs";
import {useScenario} from "./scenarios.mjs";

const out = process.env.SHOTS;
const VIEWS = [
  ["index", ""], ["routes", ""], ["routes", "#60"], ["ontime", ""], ["quality", ""],
  ["data", ""], ["methods", ""], ["codebook", ""], ["glossary", ""],
];

test.skip(!out, "set SHOTS=<folder> to take pictures");

for (const [name, hash] of VIEWS) {
  test(`picture of ${name}${hash}`, async ({page}, info) => {
    await useScenario(page, process.env.SCENARIO ?? "real");
    await open(page, name, {hash});
    for (const d of await page.locator("details").all()) await d.evaluate(n => (n.open = true));
    await page.waitForTimeout(300);
    await page.screenshot({
      path: `${out}/${info.project.name}/${name}${hash.replace("#", "-")}.png`,
      fullPage: true,
    });
  });
}
