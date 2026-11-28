// The jump box at the top of the home page: type a route number or a stop
// name, pick one, and go to its page (routes.html#60 or stops.html#<id>).
//
// It follows the ARIA 1.2 combobox pattern with a listbox popup: the text box
// keeps focus, aria-expanded says whether the list is open, and
// aria-activedescendant names the option the arrow keys are on.
//   Down / Up        open the list, move through the options
//   Alt + Down       open the list without moving
//   Enter            go to the option the arrows are on, or the first match
//   Escape           close the list; a second Escape clears the box
// The markup is in index.qmd, so the box takes its place before the data
// loads and the page does not shift; jumpBox only wires it up.

const MAX_SHOWN = 8;

const fold = s => String(s ?? "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
  .replace(/[^a-z0-9]+/g, " ").trim();

// How well an item matches the typed text: lower is better, null is no match.
export function score(item, query) {
  const q = fold(query).replace(/^route /, "");
  if (!q) return null;
  if (item.kind === "route" && fold(item.short) === q) return 0;
  const text = fold(item.text);
  if (text.startsWith(q)) return 1;
  if (` ${text}`.includes(` ${q}`)) return 2;
  if (text.includes(q)) return 3;
  return null;
}

// The options for a query, best first; routes before stops on a tie.
export function matches(items, query, max = MAX_SHOWN) {
  return items
    .map((item, i) => ({item, i, s: score(item, query)}))
    .filter(x => x.s != null)
    .sort((a, b) => a.s - b.s || (a.item.kind === b.item.kind ? a.i - b.i : a.item.kind === "route" ? -1 : 1))
    .slice(0, max)
    .map(x => x.item);
}

// root: the element holding input[role=combobox], the listbox and a status
// line (.jump-status); items: [{kind, label, sub, href, short, text}].
export function jumpBox(root, items, {go = href => { location.href = href; }} = {}) {
  const input = root.querySelector("[role=combobox]");
  const list = root.querySelector("[role=listbox]");
  const status = root.querySelector(".jump-status");
  let shown = [];
  let active = -1;

  const setActive = i => {
    active = i;
    [...list.children].forEach((li, k) => {
      li.setAttribute("aria-selected", String(k === i));
      li.classList.toggle("active", k === i);
    });
    if (i >= 0) {
      input.setAttribute("aria-activedescendant", list.children[i].id);
      list.children[i].scrollIntoView?.({block: "nearest"});
    } else input.removeAttribute("aria-activedescendant");
  };

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    setActive(-1);
  };

  const open = () => {
    const q = input.value;
    shown = q.trim() ? matches(items, q) : items.filter(x => x.kind === "route").slice(0, MAX_SHOWN);
    list.replaceChildren(...shown.map((item, k) => {
      const li = document.createElement("li");
      li.id = `${list.id}-${k}`;
      li.setAttribute("role", "option");
      li.setAttribute("aria-selected", "false");
      const kind = document.createElement("span");
      kind.className = "jump-kind";
      kind.textContent = item.kind === "route" ? "Route" : "Stop";
      const label = document.createElement("span");
      label.className = "jump-label";
      label.textContent = item.label;
      li.append(kind, " ", label);
      if (item.sub) {
        const sub = document.createElement("span");
        sub.className = "jump-sub";
        sub.textContent = item.sub;
        li.append(" ", sub);
      }
      // Keep focus in the box while the pointer picks an option.
      li.addEventListener("mousedown", e => e.preventDefault());
      li.addEventListener("click", () => go(item.href));
      return li;
    }));
    const empty = shown.length === 0;
    list.hidden = empty;
    input.setAttribute("aria-expanded", String(!empty));
    setActive(-1);
    if (status) {
      status.textContent = !q.trim() ? "" : empty ? "No route or stop matches." :
        `${shown.length} ${shown.length === 1 ? "match" : "matches"}. Use the arrow keys to pick one.`;
    }
  };

  input.addEventListener("input", open);
  input.addEventListener("keydown", e => {
    const isOpen = !list.hidden;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!isOpen) { open(); if (e.altKey) return; }
      if (shown.length) setActive(active < shown.length - 1 ? active + 1 : 0);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!isOpen) open();
      if (shown.length) setActive(active > 0 ? active - 1 : shown.length - 1);
    } else if (e.key === "Enter") {
      const pick = active >= 0 ? shown[active] : input.value.trim() ? matches(items, input.value, 1)[0] : null;
      if (pick) { e.preventDefault(); go(pick.href); }
    } else if (e.key === "Escape") {
      if (isOpen) { e.preventDefault(); close(); }
      else if (input.value) { e.preventDefault(); input.value = ""; if (status) status.textContent = ""; }
    }
  });
  input.addEventListener("blur", close);
  input.disabled = false;
  root.classList.add("ready");
  return {input, list, open, close};
}

// The items: every route (from the route map), then every timetable stop
// (views/stops/index.json), each saying where it goes.
export function jumpItems({routes = [], stops = [], routeTitle, routeSub, routeSlug, longName}) {
  const byId = Object.fromEntries(routes.map(r => [r.route_id, r]));
  const routeItems = routes.map(r => {
    const title = routeTitle(r);
    return {kind: "route", label: title, sub: routeSub(r), short: r.short_name || "", href: `routes.html#${routeSlug(r, r.route_id)}`,
      text: `${r.short_name ?? ""} ${title} ${routeSub(r)} ${longName(r.long_name) ?? ""}`};
  });
  const stopItems = stops.map(s => {
    const names = (s.routes ?? []).map(id => byId[id]?.short_name || longName(byId[id]?.long_name) || id);
    const sub = names.length ? `${names.length === 1 ? "Route" : "Routes"} ${names.join(", ")}` : "";
    return {kind: "stop", label: s.name, sub, short: "", href: `stops.html#${encodeURIComponent(s.stop_id)}`, text: s.name};
  });
  return [...routeItems, ...stopItems];
}
