// Route map: Island Transit's route lines (from its own schedule files,
// maps/*.json on the data branch) on OpenStreetMap tiles, drawn with Leaflet.
//
// Each line is colored by its on-time band (lib.js BANDS). Color is never the
// only cue: the key beside the map names the bands, every line names its
// route and its figure when pointed at, and the stops of the picked route are
// drawn in their band's shape.
//
// One map is made per page and kept: it measures its own box, so resizing the
// window or turning a phone neither rebuilds it nor undoes the reader's
// zooming, and `destroy()` takes it down cleanly.

import {NO_BAND, band, bandIconHTML, lateness, pct, plural, routeSub, routeTitle} from "./lib.js";

const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const LINE = 4, LINE_BOLD = 7, CASING = 7, CASING_BOLD = 10;
// A finger needs a wider target than the eye: an unseen line this wide lies
// over each route and takes the pointing and tapping.
const HIT = 24;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}

// "67% on time, typically 3.2 min late", or why there is no figure.
export function routeSummary(stats) {
  const b = band(stats?.on_time_share_of_observed, stats?.observed);
  return stats && b !== NO_BAND
    ? `${pct(stats.on_time_share_of_observed)} on time, typically ${lateness(stats.deviation_s_median)}`
    : "Too few departures seen to rate";
}

function stopSummary(s) {
  return band(s.on_time_share_of_observed, s.observed) === NO_BAND
    ? `${plural(s.observed, "departure")} seen, too few to rate`
    : `${pct(s.on_time_share_of_observed)} on time, typically ${lateness(s.deviation_s_median)}`;
}

function routeTip(r, stats) {
  const sub = routeSub(r);
  return `<strong>${esc(routeTitle(r))}</strong>${sub ? `<br>${esc(sub)}` : ""}<br>${esc(routeSummary(stats))}`;
}

// drawRouteMap(L, mapData, recent, {onSelect, linkFor, onStop})
//   onStop(stopId)     called when a stop marker of the picked route is pointed
//                      at or focused, and with null when it no longer is
//   plain              draw every line in one neutral color, named but
//                      without figures (a page that draws its own marks)
//   label              the map's name for screen readers
//   onSelect(routeId)  called when a route line is clicked or tapped
//   linkFor(routeId)   if given, a tap on a touch screen opens a small card
//                      with a link to this address instead of calling
//                      onSelect, so a finger resting on the map never leaves
//                      the page by accident
// Returns {node, map, setSelected(id), highlight(id), destroy()}.
export function drawRouteMap(L, mapData, recent, {onSelect = null, linkFor = null, onStop = null, plain = false, label = null} = {}) {
  const node = document.createElement("div");
  node.className = "route-map";
  node.setAttribute("role", "region");
  node.setAttribute("aria-label", label ??
    "Map of Island Transit routes, colored by how often buses were on time. The same figures are listed beside the map.");
  // The box is bounded here, not only in the stylesheet. A page reader that
  // skips stylesheets would otherwise get a box as tall as its contents: each
  // tile makes it taller, a taller map asks for more tiles, and it ends with
  // the whole world requested from OpenStreetMap.
  node.style.height = "var(--map-height, 440px)";
  node.style.overflow = "hidden";

  const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
  // Whole zoom levels only: fractional zoom leaves hairline seams between tiles.
  const m = L.map(node, {
    scrollWheelZoom: false, zoomSnap: 1, attributionControl: true,
    zoomAnimation: !calm, fadeAnimation: !calm, markerZoomAnimation: !calm,
    // On a touch screen one finger scrolls the page; two move the map.
    dragging: !L.Browser.mobile, tap: false,
  });
  m.attributionControl.setPrefix(false);

  const [w, s, e, n] = mapData.bbox;
  const all = L.latLngBounds([[s, w], [n, e]]);
  let view = {bounds: all, padding: [8, 8], maxZoom: 12};
  // True once the reader has zoomed or moved the map themselves. From then
  // on a change in the map's size keeps their view instead of refitting.
  let moved = false, fitting = false;
  const fit = () => {
    fitting = true;
    m.fitBounds(view.bounds, {padding: view.padding, maxZoom: view.maxZoom, animate: false});
    fitting = false;
    moved = false;
  };
  m.on("zoomstart dragstart", () => { if (!fitting) moved = true; });

  const stats = recent?.routes ?? {};
  const layers = {};
  const casing = L.layerGroup().addTo(m);
  const lines = L.layerGroup().addTo(m);
  const hits = L.layerGroup().addTo(m);
  const stopLayer = L.layerGroup().addTo(m);
  let selected = null, hovered = null;

  function restyle() {
    for (const [id, l] of Object.entries(layers)) {
      const bold = id === selected || id === hovered;
      const dim = selected != null && id !== selected && id !== hovered;
      l.line.setStyle({weight: bold ? LINE_BOLD : LINE});
      l.under.setStyle({weight: bold ? CASING_BOLD : CASING});
      l.line.getElement()?.classList.toggle("dim", dim);
      l.under.getElement()?.classList.toggle("dim", dim);
    }
    const top = layers[hovered] ?? layers[selected];
    if (top) {
      top.under.bringToFront();
      top.line.bringToFront();
    }
  }

  // Draw routes with the most trips last, so busy routes sit on top.
  for (const r of [...(mapData.routes ?? [])].sort((a, b) => a.trips - b.trips)) {
    const st = stats[r.route_id];
    const b = plain ? NO_BAND : band(st?.on_time_share_of_observed, st?.observed);
    const latlngs = r.paths.map(p => p.map(([lon, lat]) => [lat, lon]));
    const under = L.polyline(latlngs, {className: "route-casing", weight: CASING, interactive: false});
    const line = L.polyline(latlngs, {className: `route-line band-${b.key}`, weight: LINE, interactive: false});
    const hit = L.polyline(latlngs, {className: "route-hit", weight: HIT, opacity: 0});
    hit.bindTooltip(plain ? `<strong>${esc(routeTitle(r))}</strong>` : routeTip(r, st), {sticky: true, className: "map-tip"});
    hit.on("mouseover", () => { hovered = r.route_id; restyle(); });
    hit.on("mouseout", () => { hovered = null; restyle(); });
    hit.on("click", ev => {
      const touch = ev.originalEvent?.pointerType === "touch";
      if (touch && linkFor) {
        hit.closeTooltip();
        L.popup({className: "map-card", closeButton: true, autoPanPadding: [16, 16]})
          .setLatLng(ev.latlng)
          .setContent(`${routeTip(r, st)}<br><a href="${esc(linkFor(r.route_id))}">See ${esc(routeTitle(r))} stop by stop</a>`)
          .openOn(m);
      } else if (onSelect) {
        onSelect(r.route_id);
      }
    });
    under.addTo(casing);
    line.addTo(lines);
    hit.addTo(hits);
    layers[r.route_id] = {line, under, bounds: line.getBounds()};
  }

  // The tiles are added once the map's box has a size (see the end).
  let tiles = null;
  const stopsById = Object.fromEntries((mapData.timepoint_stops ?? []).map(s => [s.stop_id, s]));

  // The picked route's stop markers, by stop id, for highlightStop.
  let markers = {};
  function setSelected(id) {
    selected = id != null && layers[id] ? id : null;
    stopLayer.clearLayers();
    markers = {};
    restyle();
    if (selected == null) {
      view = {bounds: all, padding: [8, 8], maxZoom: 12};
      if (tiles) fit();
      return;
    }
    for (const s of stats[selected]?.stops ?? []) {
      const pos = stopsById[s.stop_id];
      if (!pos) continue;
      const b = band(s.on_time_share_of_observed, s.observed);
      // A marker, not a drawn circle: it can take the keyboard focus, and
      // focusing it shows the same tip that pointing at it does.
      const marker = L.marker([pos.lat, pos.lon], {
        icon: L.divIcon({className: "stop-marker", html: bandIconHTML(b, 18), iconSize: [28, 28]}),
        keyboard: true, riseOnHover: true,
      }).bindTooltip(`<strong>${esc(s.name)}</strong><br>${esc(stopSummary(s))}`, {className: "map-tip", direction: "auto", offset: [0, -10]});
      marker.on("add", () => marker.getElement()?.setAttribute("aria-label", `${s.name}: ${stopSummary(s)}`));
      for (const on of ["mouseover", "focus"]) marker.on(on, () => onStop?.(s.stop_id));
      for (const off of ["mouseout", "blur"]) marker.on(off, () => onStop?.(null));
      marker.addTo(stopLayer);
      markers[s.stop_id] = marker;
    }
    view = {bounds: layers[selected].bounds, padding: [28, 28], maxZoom: 14};
    if (tiles) fit();
  }

  // Make one route stand out without selecting it (a link being pointed at).
  function highlight(id) {
    hovered = id != null && layers[id] ? id : null;
    restyle();
  }

  // Leaflet needs to know the size of its box, and the box has none until the
  // page lays it out. Fit the view the first time the box has a size, and
  // only then add the tiles, so each tile is fetched once.
  //
  // The tiles come from another server. They are added only once the page
  // itself has finished loading, so a slow or silent tile server can never
  // hold up the page's load event (and everything that waits for it); the
  // route lines, which come with the page, are drawn at once.
  //
  // Without Leaflet's stylesheet the tiles would be laid out as a column of
  // pictures, not a map, so none are fetched then.
  const styled = () => getComputedStyle(m.getPane("mapPane")).position === "absolute";
  let size = "", destroyed = false;
  const afterLoad = start => {
    if (document.readyState === "complete") start();
    else addEventListener("load", start, {once: true});
  };
  const watcher = new ResizeObserver(() => {
    const now = `${node.clientWidth}x${node.clientHeight}`;
    if (node.clientWidth === 0 || now === size) return;
    size = now;
    m.invalidateSize({animate: false});
    if (!tiles) {
      fit();
      tiles = L.tileLayer(TILES, {maxZoom: 18, attribution: ATTRIBUTION});
      afterLoad(() => { if (!destroyed && styled()) tiles.addTo(m); });
      restyle();
    } else if (!moved) {
      fit();
    }
  });
  watcher.observe(node);

  function destroy() {
    destroyed = true;
    watcher.disconnect();
    m.remove();
  }

  // Point out one stop of the picked route (its row in the stop chart is
  // being pointed at): its tip opens and its marker grows. null clears it.
  let shown = null;
  function highlightStop(id) {
    if (shown && markers[shown]) {
      markers[shown].closeTooltip();
      markers[shown].getElement()?.classList.remove("stop-on");
    }
    shown = id != null && markers[id] ? id : null;
    if (shown) {
      markers[shown].openTooltip();
      markers[shown].getElement()?.classList.add("stop-on");
    }
  }

  return {node, map: m, setSelected, highlight, highlightStop, destroy};
}
