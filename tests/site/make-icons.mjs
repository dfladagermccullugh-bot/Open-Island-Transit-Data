// Draws the PNG icons and the link-preview card from site/favicon.svg, so
// they cannot drift apart from it or from the site's colors. Run after
// changing the SVG or the palette:  node make-icons.mjs
//
//   node make-icons.mjs --card-from <headline.json> --out <dir>
//
// draws only the card with the latest figure (ontime/headline.json) into
// <dir>/social-card-figure.png, with what it shows in social-card-figure.json.
// The Pages job runs it on the rendered site; scripts/prerender.py then names
// the card by its hash and points the pages' previews at it. The fixed card
// above, with no figure, stays in the site as the fallback.
import {chromium} from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../site");
const svg = fs.readFileSync(path.join(site, "favicon.svg"), "utf8");
const args = process.argv.slice(2);
const arg = name => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const browser = await chromium.launch(process.env.PW_CHROMIUM ? {executablePath: process.env.PW_CHROMIUM} : {});

// The card's frame: 1200 by 630 pixels, the site's surface, text and link
// colors (site/styles.scss).
const STYLE = `
  html, body { margin: 0; }
  body { width: 1200px; height: 630px; box-sizing: border-box; background: #fcfcfb; color: #0b0b0b;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", "Noto Sans", Arial, sans-serif;
    padding: 76px 80px 0; border-bottom: 14px solid #1f66c1; display: flex; flex-direction: column; }
  .name { display: flex; align-items: center; gap: 22px; font-size: 32px; font-weight: 650; color: #52514e; }
  .name svg { width: 84px; height: 84px; display: block; }
  p.foot { font-size: 27px; color: #52514e; margin: auto 0 62px; white-space: nowrap; }`;
const NAME = `<div class="name">${svg}<span>Open Island Transit Data</span></div>`;

async function shoot(html, file) {
  const card = await browser.newPage({viewport: {width: 1200, height: 630}});
  await card.setContent(html);
  await card.screenshot({path: file});
  await card.close();
}

if (arg("--card-from")) {
  // Formatted as site/lib.js formats them (pct, longDate), and as
  // scripts/prerender.py does, which checks the two agree.
  const head = JSON.parse(fs.readFileSync(arg("--card-from"), "utf8"));
  const out = arg("--out") ?? ".";
  const c = head.current ?? {};
  if (!c.observed || c.on_time_share_of_observed == null) {
    console.log("make-icons: the headline has no figure yet; no card drawn");
    await browser.close();
    process.exit(0);
  }
  const text = (100 * c.on_time_share_of_observed).toFixed(0);
  const share = `${+text === 0 ? text.replace("-", "") : text}%`;
  const last = new Date(`${head.last_service_date}T12:00:00Z`)
    .toLocaleDateString("en-US", {month: "long", day: "numeric", year: "numeric", timeZone: "UTC"});
  const period = c.days === 1 ? `on ${last}` : `over the last ${c.days} days with service, to ${last}`;
  const esc = t => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  await shoot(`<style>${STYLE}
    .figure { display: flex; align-items: flex-end; gap: 36px; margin: 54px 0 0; }
    .big { font-size: 200px; line-height: .82; font-weight: 700; letter-spacing: -.03em; color: #0b0b0b; font-variant-numeric: tabular-nums; }
    .what { font-size: 40px; line-height: 1.2; font-weight: 650; max-width: 640px; }
    .when { font-size: 30px; line-height: 1.3; color: #52514e; margin: 30px 0 0; }
  </style>
  ${NAME}
  <div class="figure"><div class="big">${esc(share)}</div><div class="what">of Island Transit's bus departures left on time</div></div>
  <p class="when">${esc(period[0].toUpperCase() + period.slice(1))}. On time is 1 minute early to 5 minutes late.</p>
  <p class="foot">islandtransitdata.org · Not affiliated with Island Transit.</p>`, path.join(out, "social-card-figure.png"));
  fs.writeFileSync(path.join(out, "social-card-figure.json"),
    JSON.stringify({share, period, last_service_date: head.last_service_date}) + "\n");
  console.log(`make-icons: card drawn with ${share} ${period}`);
  await browser.close();
  process.exit(0);
}

for (const [name, size, rounded] of [["favicon-32.png", 32, true], ["apple-touch-icon.png", 180, false]]) {
  const page = await browser.newPage({viewport: {width: size, height: size}});
  // iOS rounds the corners itself and shows black behind a transparent icon,
  // so the touch icon is a full square.
  const art = rounded ? svg : svg.replace('rx="14"', 'rx="0"');
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style>${art}`);
  await page.screenshot({path: path.join(site, name), omitBackground: true});
  await page.close();
}

// The fixed card, shown when a link to the site is shared and no card with
// the figure was drawn: no figure that would go out of date.
await shoot(`<style>${STYLE}
  h1 { font-size: 92px; line-height: 1.08; font-weight: 700; letter-spacing: -.015em; margin: 58px 0 0; max-width: 1000px; }
</style>
${NAME}
<h1>How on time are Island Transit's buses?</h1>
<p class="foot">Saved from the live feed every 20 seconds. Not affiliated with Island Transit.</p>`, path.join(site, "social-card.png"));
await browser.close();
