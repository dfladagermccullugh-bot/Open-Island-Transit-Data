// Shared loaders for the site. Files come from the repository's `data`
// branch, copied into data/ when the site is built. Missing files (no data
// yet) give empty results instead of errors.

export async function csv(d3, path) {
  try {
    const rows = await d3.csv(path, d3.autoType);
    return rows;
  } catch (e) {
    return [];
  }
}

export async function json(path) {
  try {
    const r = await fetch(path);
    return r.ok ? await r.json() : null;
  } catch (e) {
    return null;
  }
}

export function pct(x, digits = 0) {
  return x == null || Number.isNaN(x) ? "–" : `${(100 * x).toFixed(digits)}%`;
}

export function num(x, digits = 0) {
  return x == null || Number.isNaN(x) ? "–" : Number(x).toFixed(digits);
}

export function routeLabel(routes, id) {
  const r = routes && routes[id];
  if (!r) return id;
  return r.short_name ? `${r.short_name} ${r.long_name}` : r.long_name || id;
}

export const SERIES = "var(--series-1)";
