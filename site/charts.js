// Charts and tables shared by every page.
//
// A page says what to draw; this module decides how. Every chart measures the
// space it is given and redraws when that space changes, so no page cell
// depends on the window width, and resizing or turning a phone never resets
// what the reader picked or opened. Every chart also carries a short
// description for screen readers, and tables are real tables that can be
// sorted from the keyboard.

import {NARROW, DOTS_UP_TO, SERIES, dateAxis, emptyNote, h, num, pct} from "./lib.js";

const FRAME = {background: "transparent", fontSize: "12px", overflow: "visible"};
const RULE = "var(--rule)";
const CHAR_PX = 6.6; // an average letter at the charts' 12px type

// The widest a category label gets, in pixels, for a chart's left margin.
function labelWidth(labels, most = 280) {
  return Math.min(most, Math.ceil(Math.max(0, ...labels.map(l => String(l).length)) * CHAR_PX) + 14);
}

// A share axis that fits the values. Most shares read best from 0 to 100%.
// Rates that stay small (a 0.2% failure rate) would lie flat along the bottom
// of that axis, so they get an axis of their own that still starts at zero.
export function shareDomain(values) {
  const top = Math.max(0, ...values.filter(v => v != null && !Number.isNaN(v)));
  if (top > 0.2) return [0, 1];
  const steps = [0.01, 0.02, 0.05, 0.1, 0.2];
  return [0, steps.find(s => s >= top * 1.25) ?? 0.2];
}

// The percent label for an axis tick; small axes need a decimal.
const shareTick = domain => d => pct(d, domain[1] <= 0.05 ? 1 : 0);

// Observable Plot names each layer of a chart ("line", "dot") for screen
// readers, which is noise next to the description we write. Present the chart
// as one image with that description, and drop the layer names.
function asImage(node) {
  const svgs = node.matches?.("svg") ? [node] : [...(node.querySelectorAll?.("svg") ?? [])];
  for (const svg of svgs) {
    if (!svg.hasAttribute("aria-label")) continue;
    svg.setAttribute("role", "img");
    for (const part of svg.querySelectorAll("[aria-label], [aria-description]")) {
      part.removeAttribute("aria-label");
      part.removeAttribute("aria-description");
    }
  }
  return node;
}

// A chart that fits its container. `render(width, narrow)` returns the chart;
// it runs once the container has a width and again when the width changes.
// `height` reserves the space beforehand, so the page does not jump.
export function responsive(render, {height = 0, min = 280, max = 960} = {}) {
  const slot = h("div", {class: "chart-slot"});
  if (height) slot.style.minHeight = `${height}px`;
  let drawn = 0, timer = null, attached = false;
  const draw = () => {
    const space = Math.round(slot.clientWidth);
    if (!space || space === drawn) return;
    drawn = space;
    let node;
    try {
      node = asImage(render(Math.max(min, Math.min(space, max)), space < NARROW));
    } catch (e) {
      console.warn("a chart could not be drawn:", e);
      node = emptyNote("unavailable", "This chart");
    }
    slot.replaceChildren(node);
    slot.style.minHeight = "";
  };
  const watcher = new ResizeObserver(() => {
    if (!slot.isConnected) {
      if (attached) watcher.disconnect();
      return;
    }
    attached = true;
    clearTimeout(timer);
    if (drawn) timer = setTimeout(draw, 100);
    else draw();
  });
  watcher.observe(slot);
  return slot;
}

// A chart with its title, an optional figure beside the title, a note, a key
// and a table of the same numbers folded underneath.
export function figure({title = null, value = null, note = null, legend = null, chart, table = null, tableLabel = "Show these figures as a table"}) {
  return h("figure", {class: "chart"},
    title ? h("figcaption", {class: "chart-title"}, title, value != null ? h("span", {class: "chart-value"}, value) : null) : null,
    note ? h("p", {class: "note"}, note) : null,
    legend,
    chart,
    table ? h("details", {class: "as-table"}, h("summary", {}, tableLabel), table) : null);
}

// A real table: a caption, column headers that sort when pressed (by mouse or
// keyboard), numbers lined up on the right, and long tables folded to their
// first rows.
//
//   columns: [{key, label, format, align, sortValue, sortable}]
//   sort:    {key, descending}
export function table(rows, {columns, caption, sort = null, pageSize = 25, wide = false}) {
  const cols = columns.filter(Boolean).map(c => {
    const sample = rows.map(r => r[c.key]).find(v => v != null);
    const numeric = c.align ? c.align === "right" : typeof sample === "number";
    return {format: v => (v == null ? "–" : String(v)), sortValue: r => r[c.key], sortable: true, ...c, numeric};
  });
  let order = sort ? {...sort} : null;
  let showAll = rows.length <= pageSize + 3;

  const tbody = h("tbody");
  const headers = cols.map(c => {
    const th = h("th", {scope: "col", class: c.numeric ? "num" : null});
    if (!c.sortable || rows.length < 2) {
      th.append(c.label);
      return th;
    }
    const button = h("button", {type: "button", class: "sort"}, c.label, h("span", {class: "sort-mark", "aria-hidden": "true"}));
    button.addEventListener("click", () => {
      order = {key: c.key, descending: order?.key === c.key ? !order.descending : c.numeric};
      fill();
    });
    th.append(button);
    return th;
  });
  const more = h("button", {type: "button", class: "show-more"});
  more.addEventListener("click", () => {
    showAll = true;
    fill();
  });

  function compare(a, b) {
    if (a == null || (typeof a === "number" && Number.isNaN(a))) return b == null ? 0 : 1;
    if (b == null || (typeof b === "number" && Number.isNaN(b))) return -1;
    return typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b), "en", {numeric: true});
  }

  function fill() {
    let sorted = rows;
    if (order) {
      const col = cols.find(c => c.key === order.key);
      const sign = order.descending ? -1 : 1;
      sorted = [...rows].sort((r, s) => {
        const [a, b] = [col.sortValue(r), col.sortValue(s)];
        // Missing values go last whichever way the column is sorted.
        if (a == null || b == null) return compare(a, b);
        return sign * compare(a, b);
      });
    }
    cols.forEach((c, i) => {
      const on = order?.key === c.key;
      if (c.sortable && rows.length >= 2) {
        headers[i].setAttribute("aria-sort", on ? (order.descending ? "descending" : "ascending") : "none");
      }
    });
    const shown = showAll ? sorted : sorted.slice(0, pageSize);
    tbody.replaceChildren(...shown.map(r => h("tr", {}, ...cols.map((c, i) => {
      const cell = h(i === 0 && !c.numeric ? "th" : "td", {class: c.numeric ? "num" : null, scope: i === 0 && !c.numeric ? "row" : null});
      cell.append(c.format(r[c.key], r) ?? "–");
      return cell;
    }))));
    more.hidden = showAll;
    more.textContent = `Show all ${rows.length.toLocaleString("en-US")} rows`;
  }
  fill();

  const el = h("table", {class: wide ? "data-table wide" : "data-table"},
    h("caption", {class: "visually-hidden"}, caption),
    h("thead", {}, h("tr", {}, ...headers)),
    tbody);
  // A wide table scrolls sideways inside its own box; the box can take the
  // keyboard focus so it can be scrolled without a mouse.
  const scroller = h("div", {class: "table-scroll", role: "region", "aria-label": caption, tabindex: "0"}, el);
  return h("div", {class: "table-wrap"}, scroller, more);
}

export function makeCharts({Plot}) {
  // A line over service days. `y` names the column. With `share` the values
  // are shares, drawn from 0 to 100% (or less for rates that stay small);
  // without it they are plain numbers, drawn from zero up.
  function trend(rows, {y, format = v => num(v, 1), share = true, yLabel = null, height = 220,
                        maxTicks = 8, ariaLabel, describe = "", tip = null}) {
    const dom = share ? shareDomain(rows.map(d => d[y])) : undefined;
    return responsive(width => {
      const axis = dateAxis(rows.map(d => d.date), maxTicks, width);
      return Plot.plot({
        ariaLabel, ariaDescription: describe, style: FRAME, width, height,
        marginLeft: share && dom[1] <= 0.05 ? 54 : 48, marginRight: 24, marginTop: yLabel ? 26 : 14,
        marginBottom: axis.marginBottom,
        x: axis.x,
        y: {grid: true, label: yLabel, domain: dom, zero: true, nice: !share,
            tickFormat: share ? shareTick(dom) : undefined, ticks: 5},
        marks: [
          Plot.ruleY([0], {stroke: RULE}),
          Plot.lineY(rows, {x: "date", y, stroke: SERIES, strokeWidth: 2, defined: d => d[y] != null}),
          rows.length <= DOTS_UP_TO
            ? Plot.dot(rows, {x: "date", y, fill: SERIES, r: 3.5, stroke: "var(--surface)", strokeWidth: 1.5, filter: d => d[y] != null})
            : null,
          Plot.tip(rows, Plot.pointerX({x: "date", y, filter: d => d[y] != null,
            title: tip ?? (d => format(d[y]))})),
        ],
      });
    }, {height});
  }

  // One bar for each category. Lying down (the default), each bar has its
  // name on the left and its value printed at its end. Standing up
  // (`upright`), the bars run left to right in the order given, as for hours
  // of the day; on a phone they lie down so every label stays readable.
  function bars(rows, {category, value, format = v => pct(v), fill = SERIES, upright = false,
                       domain = [0, 1], axisLabel = null, sorted = !upright, ariaLabel, tip = null,
                       references = []}) {
    const names = rows.map(d => d[category]);
    const describe = rows.map(d => `${d[category]}: ${format(d[value])}`).join("; ");
    const ordered = sorted ? [...rows].sort((a, b) => (b[value] ?? -1) - (a[value] ?? -1)) : rows;
    const rowH = 28;
    const lying = (width, narrow) => Plot.plot({
      ariaLabel, ariaDescription: describe, style: FRAME, width,
      height: (narrow ? 46 : rowH) * rows.length + 46,
      marginLeft: narrow ? 4 : labelWidth(names), marginRight: 52, marginTop: narrow ? 22 : 6, marginBottom: 40,
      x: {domain, grid: true, label: axisLabel, labelAnchor: "center", labelOffset: 34, tickFormat: shareTick(domain), ticks: narrow ? 3 : 5},
      y: {domain: ordered.map(d => d[category]), label: null, axis: narrow ? null : "left", tickSize: 0, padding: narrow ? 0.45 : 0.25},
      marks: [
        ...references.map(r => Plot.ruleX([r], {stroke: RULE, strokeDasharray: "3 3"})),
        Plot.barX(ordered, {y: category, x: value, fill, rx: 2}),
        Plot.ruleX([0], {stroke: RULE}),
        Plot.text(ordered, {y: category, x: d => d[value] ?? 0, text: d => format(d[value]), textAnchor: "start", dx: 6, fill: "currentColor"}),
        narrow ? Plot.text(ordered, {y: category, x: domain[0], text: category, textAnchor: "start", dy: -17, fill: "currentColor"}) : null,
        Plot.tip(ordered, Plot.pointerY({y: category, x: value, title: tip ?? (d => `${d[category]}\n${format(d[value])}`)})),
      ],
    });
    const standing = width => {
      // Label only as many bars as fit side by side.
      const every = Math.max(1, Math.ceil(rows.length * 44 / (width - 70)));
      const roomy = (width - 70) / rows.length >= 34;
      return Plot.plot({
        ariaLabel, ariaDescription: describe, style: FRAME, width, height: 270,
        marginLeft: 48, marginRight: 12, marginTop: 34, marginBottom: 30,
        x: {label: null, domain: names, padding: 0.25, tickSize: 0, ticks: names.filter((_, i) => i % every === 0)},
        y: {grid: true, label: axisLabel, domain, tickFormat: shareTick(domain), ticks: 5},
        marks: [
          ...references.map(r => Plot.ruleY([r], {stroke: RULE, strokeDasharray: "3 3"})),
          Plot.barY(rows, {x: category, y: value, fill, rx: 2}),
          Plot.ruleY([0], {stroke: RULE}),
          roomy ? Plot.text(rows, {x: category, y: d => d[value] ?? 0, text: d => format(d[value]), dy: -8, fill: "currentColor"}) : null,
          Plot.tip(rows, Plot.pointerX({x: category, y: value, title: tip ?? (d => `${d[category]}\n${format(d[value])}`)})),
        ],
      });
    };
    return responsive((width, narrow) => (upright && !narrow ? standing(width) : lying(width, narrow)),
      {height: upright ? 260 : rowH * rows.length + 46});
  }

  // One row for each place: a bar from `lo` to `hi`, a mark at `mid` in the
  // shape and color of the row's band, and a figure printed at the right.
  // `window` shades the range that counts as good.
  function dotRange(rows, {label, mid, lo, hi, band, text, window: good = null, axisLabel, ariaLabel, describe, tip}) {
    const min = Math.min(good ? good[0] - 1 : 0, ...rows.map(d => d[lo])) - 0.5;
    const max = Math.max(good ? good[1] + 3 : 0, ...rows.map(d => d[hi])) + 0.5;
    const ids = rows.map((_, i) => i);
    const keyed = rows.map((d, i) => ({...d, row: i}));
    const filled = keyed.filter(d => d[band].shape !== "ring");
    const rings = keyed.filter(d => d[band].shape === "ring");
    return responsive((width, narrow) => Plot.plot({
      ariaLabel, ariaDescription: describe, style: FRAME, width,
      height: (narrow ? 48 : 34) * rows.length + 62,
      marginLeft: narrow ? 6 : labelWidth(rows.map(d => d[label]), 300), marginRight: 56,
      marginTop: narrow ? 26 : 10, marginBottom: 46,
      x: {domain: [min, max], grid: true, label: axisLabel, labelAnchor: "center", labelOffset: 38, ticks: narrow ? 5 : 8},
      y: {domain: ids, tickFormat: i => rows[i][label], label: null, axis: narrow ? null : "left", tickSize: 0},
      marks: [
        good ? Plot.rectX([0], {x1: good[0], x2: good[1], fill: "var(--status-good)", fillOpacity: 0.13}) : null,
        Plot.ruleX([0], {stroke: RULE}),
        Plot.ruleY(keyed, {y: "row", x1: lo, x2: hi, stroke: "var(--range)", strokeWidth: 3, strokeLinecap: "round"}),
        Plot.dot(filled, {y: "row", x: mid, r: 6.5, symbol: d => d[band].shape, fill: d => d[band].color, stroke: "var(--surface)", strokeWidth: 1.5}),
        Plot.dot(rings, {y: "row", x: mid, r: 5, fill: "var(--surface)", stroke: d => d[band].color, strokeWidth: 2.5}),
        Plot.text(keyed, {y: "row", frameAnchor: "right", dx: 50, text, textAnchor: "end", fill: "currentColor"}),
        narrow ? Plot.text(keyed, {y: "row", x: min, text: label, textAnchor: "start", dy: -16, fill: "currentColor"}) : null,
        Plot.tip(keyed, Plot.pointerY({y: "row", x: mid, title: tip})),
      ],
    }), {height: 34 * rows.length + 62});
  }

  return {trend, bars, dotRange, table, figure, responsive};
}
