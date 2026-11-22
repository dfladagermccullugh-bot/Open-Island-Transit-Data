// Draws the PNG icons and the link-preview card from site/favicon.svg, so
// they cannot drift apart from it or from the site's colors. Run after
// changing the SVG or the palette:  node make-icons.mjs
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

// The card shown when a link to the site is shared: 1200 by 630 pixels, the
// site's surface, text and link colors (site/styles.scss), no figure that
// would go out of date.
const card = await browser.newPage({viewport: {width: 1200, height: 630}});
await card.setContent(`<style>
  html, body { margin: 0; }
  body { width: 1200px; height: 630px; box-sizing: border-box; background: #fcfcfb; color: #0b0b0b;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", "Noto Sans", Arial, sans-serif;
    padding: 76px 80px 0; border-bottom: 14px solid #1f66c1; display: flex; flex-direction: column; }
  .name { display: flex; align-items: center; gap: 22px; font-size: 32px; font-weight: 650; color: #52514e; }
  .name svg { width: 84px; height: 84px; display: block; }
  h1 { font-size: 92px; line-height: 1.08; font-weight: 700; letter-spacing: -.015em; margin: 58px 0 0; max-width: 1000px; }
  p { font-size: 27px; color: #52514e; margin: auto 0 62px; white-space: nowrap; }
</style>
<div class="name">${svg}<span>Open Island Transit Data</span></div>
<h1>How on time are Island Transit's buses?</h1>
<p>Saved from the live feed every 20 seconds. Not affiliated with Island Transit.</p>`);
await card.screenshot({path: path.join(site, "social-card.png")});
await card.close();
await browser.close();
