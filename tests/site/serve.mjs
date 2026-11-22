// Serves the rendered site the way GitHub Pages does: under the repository's
// path, with 404.html for anything missing. Run by playwright.config.mjs.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import {fileURLToPath} from "node:url";

export const BASE = "/Open-Island-Transit-Data/";
export const PORT = Number(process.env.SITE_PORT ?? 4173);
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, process.env.SITE_DIR ?? "../../site/_site");

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".csv": "text/csv", ".svg": "image/svg+xml",
  ".png": "image/png", ".xml": "application/xml", ".txt": "text/plain",
  ".woff": "font/woff", ".woff2": "font/woff2",
};

function send(res, status, file) {
  res.writeHead(status, {
    "content-type": TYPES[path.extname(file)] ?? "application/octet-stream",
    "cache-control": "no-store",
  });
  fs.createReadStream(file).pipe(res);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  http.createServer((req, res) => {
    const url = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (!url.startsWith(BASE)) {
      res.writeHead(302, {location: BASE});
      res.end();
      return;
    }
    let file = path.join(root, url.slice(BASE.length));
    if (!file.startsWith(root)) file = root;
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, "index.html");
    if (fs.existsSync(file)) send(res, 200, file);
    else send(res, 404, path.join(root, "404.html"));
  }).listen(PORT, "127.0.0.1");
}
