// Route map: Island Transit's route lines (from its own schedule files,
// maps/*.json on the data branch) on OpenStreetMap tiles, drawn with
// Leaflet. Each line is colored by its on-time band (lib.js BANDS); the
// legend always names the bands, so color is never the only cue.

import {BANDS, NO_BAND, band, routeTitle, routeSub, lateness, pct, plural} from "./lib.js";

const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

function routeTip(r, stats) {
  const b = band(stats?.on_time_share_of_observed, stats?.observed);
  const sub = routeSub(r);
  const line = stats && b !== NO_BAND
    ? `${pct(stats.on_time_share_of_observed)} on time · typically ${lateness(stats.deviation_s_median)}`
    : "Too few departures seen to rate";
  return `<strong>${esc(routeTitle(r))}</strong>${sub ? `<br>${esc(sub)}` : ""}<br>${esc(line)}`;
}

function stopTip(s) {
  const b = band(s.on_time_share_of_observed, s.observed);
  const line = b === NO_BAND
    ? `${plural(s.observed, "departure")} seen: too few to rate`
    : `${pct(s.on_time_share_of_observed)} on time · typically ${lateness(s.deviation_s_median)}`;
  return `<strong>${esc(s.name)}</strong><br>${esc(line)}`;
}

export function drawRouteMap(L, mapData, recent, {height = 460, onSelect = null, legend = true} = {}) {
  const node = document.createElement("div");
  node.className = "route-map";
  node.style.height = `${height}px`;
  node.setAttribute("role", "region");
  node.setAttribute("aria-label", "Map of Island Transit routes colored by how often buses were on time");

  // Whole zoom levels only: fractional zoom leaves hairline seams between tiles.
  const m = L.map(node, {scrollWheelZoom: false, zoomSnap: 1, attributionControl: true});
  L.tileLayer(TILES, {maxZoom: 18, attribution: ATTRIBUTION}).addTo(m);
  const [w, s, e, n] = mapData.bbox;
  const all = L.latLngBounds([[s, w], [n, e]]);
  let view = {bounds: all, padding: [12, 12], maxZoom: 12};
  const fit = () => m.fitBounds(view.bounds, {padding: view.padding, maxZoom: view.maxZoom});
  m.setView(all.getCenter(), 10);

  const stats = recent?.routes ?? {};
  const layers = {};
  const casing = L.layerGroup().addTo(m);
  const lines = L.layerGroup().addTo(m);
  const stopLayer = L.layerGroup().addTo(m);

  // Draw routes with the most trips last, so busy routes sit on top.
  const routes = [...mapData.routes].sort((a, b) => a.trips - b.trips);
  for (const r of routes) {
    const st = stats[r.route_id];
    const b = band(st?.on_time_share_of_observed, st?.observed);
    const latlngs = r.paths.map(p => p.map(([lon, lat]) => [lat, lon]));
    const under = L.polyline(latlngs, {className: "route-casing", weight: 7, interactive: false});
    const line = L.polyline(latlngs, {className: `route-line band-${b.key}`, weight: 4});
    line.bindTooltip(routeTip(r, st), {sticky: true, className: "map-tip"});
    line.on("mouseover", () => line.setStyle({weight: 7}));
    line.on("mouseout", () => line.setStyle({weight: layers[r.route_id].selected ? 7 : 4}));
    if (onSelect) line.on("click", () => onSelect(r.route_id));
    under.addTo(casing);
    line.addTo(lines);
    layers[r.route_id] = {line, under, bounds: line.getBounds(), selected: false};
  }

  if (legend) {
    const ctl = L.control({position: "bottomleft"});
    ctl.onAdd = () => {
      const div = L.DomUtil.create("div", "map-legend");
      const days = recent?.days ?? 0;
      const when = days > 1 ? `, last ${days} days` : days === 1 ? ", latest day" : "";
      div.innerHTML = `<div class="map-legend-title">Departures on time${when}</div>` +
        [...BANDS, NO_BAND].map(b =>
          `<div><span class="swatch band-${b.key}"></span>${esc(b.label)}</div>`).join("");
      return div;
    };
    ctl.addTo(m);
  }

  const stopsById = Object.fromEntries((mapData.timepoint_stops ?? []).map(s => [s.stop_id, s]));

  function setSelected(id) {
    stopLayer.clearLayers();
    for (const [rid, l] of Object.entries(layers)) {
      l.selected = rid === id;
      const dim = id != null && !l.selected;
      l.line.setStyle({weight: l.selected ? 7 : 4});
      l.line.getElement()?.classList.toggle("dim", dim);
      l.under.getElement()?.classList.toggle("dim", dim);
      if (l.selected) {
        l.under.bringToFront();
        l.line.bringToFront();
      }
    }
    if (id == null || !layers[id]) {
      view = {bounds: all, padding: [12, 12], maxZoom: 12};
      fit();
      return;
    }
    for (const s of stats[id]?.stops ?? []) {
      const pos = stopsById[s.stop_id];
      if (!pos) continue;
      const b = band(s.on_time_share_of_observed, s.observed);
      L.circleMarker([pos.lat, pos.lon], {radius: 7, weight: 2, className: `stop-dot band-${b.key}`})
        .bindTooltip(stopTip(s), {className: "map-tip"})
        .addTo(stopLayer);
    }
    view = {bounds: layers[id].bounds, padding: [24, 24], maxZoom: 14};
    fit();
  }

  // Leaflet measures its container when created, before the page has laid
  // it out; measure and fit again whenever its size changes.
  let size = "";
  new ResizeObserver(() => {
    const now = `${node.clientWidth}x${node.clientHeight}`;
    if (node.clientWidth === 0 || now === size) return;
    size = now;
    m.invalidateSize();
    fit();
  }).observe(node);
  return {node, map: m, setSelected};
}
