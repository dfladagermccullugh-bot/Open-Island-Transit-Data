// Build the chart library the site loads, site/vendor/plot-0.6.11/plot.js,
// from the pinned packages in package.json (Observable Plot 0.6.11, d3
// 7.8.5): only the functions build/plot-entry.js lists, so pages load far
// less than all of Plot and d3 (95 KB gzipped instead of 154 KB). Run it
// after changing that list or a version, then update its SHA-256 in
// scripts/check_site.py (VENDOR).
//
//   node tests/site/make-plot.mjs
import {build} from "esbuild";
import fs from "node:fs";
import {fileURLToPath} from "node:url";

const here = fileURLToPath(new URL(".", import.meta.url));
const out = `${here}../../site/vendor/plot-0.6.11`;
const result = await build({
  entryPoints: [`${here}build/plot-entry.js`],
  outfile: `${out}/plot.js`,
  metafile: true,
  bundle: true,
  format: "esm",
  minify: true,
  legalComments: "none",
  banner: {js: "// Observable Plot 0.6.11 and d3 7.8.5 (ISC licenses), trimmed by tests/site/make-plot.mjs."},
});

// Every package with code in the bundle keeps its license notice beside it.
const packages = new Set();
for (const input of Object.keys(result.metafile.inputs)) {
  const m = input.match(/node_modules\/((?:@[^/]+\/)?[^/]+)\//);
  if (m) packages.add(m[1]);
}
const notices = [...packages].sort().map(name => {
  const dir = `${here}node_modules/${name}`;
  const {version} = JSON.parse(fs.readFileSync(`${dir}/package.json`, "utf8"));
  const file = fs.readdirSync(dir).find(f => /^licen[cs]e/i.test(f));
  return `${name} ${version}\n\n${fs.readFileSync(`${dir}/${file}`, "utf8").trim()}\n`;
});
fs.writeFileSync(`${out}/LICENSE`, notices.join("\n" + "-".repeat(72) + "\n\n"));
console.log(`plot.js: ${packages.size} packages: ${[...packages].sort().join(", ")}`);
