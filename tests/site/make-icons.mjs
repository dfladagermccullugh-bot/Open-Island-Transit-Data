// Draws the PNG icons from site/favicon.svg, so the three icons cannot drift
// apart. Run after changing the SVG:  node make-icons.mjs
import {chromium} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../site");
const svg = fs.readFileSync(path.join(site, "favicon.svg"), "utf8");
const browser = await chromium.launch(process.env.PW_CHROMIUM ? {executablePath: process.env.PW_CHROMIUM} : {});
for (const [name, size, rounded] of [["favicon-32.png", 32, true], ["apple-touch-icon.png", 180, false]]) {
  const page = await browser.newPage({viewport: {width: size, height: size}});
  // iOS rounds the corners itself and shows black behind a transparent icon,
  // so the touch icon is a full square.
  const art = rounded ? svg : svg.replace('rx="14"', 'rx="0"');
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${art}`);
  await page.screenshot({path: path.join(site, name), omitBackground: true});
  await page.close();
}
await browser.close();
