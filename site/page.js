// Runs on every page, with or without charts: the footer's "data through"
// date, the plain-language hints on underlined terms, the live collector
// badge on the home page, and opening folded sections for printing.

import {longDate, watchLiveStatus} from "./lib.js";

// The site's own address, whatever page this is (the 404 page can be shown
// at any depth).
const ROOT = new URL(".", import.meta.url);

// --- footer ------------------------------------------------------------------

// Says which service day the figures run through. The file is written when
// the site is built (scripts/site_data.py).
async function showDataThrough() {
  const slot = document.getElementById("data-through");
  if (!slot) return;
  try {
    const r = await fetch(new URL("data/build.json", ROOT));
    const build = r.ok ? await r.json() : null;
    const text = longDate(build?.data_through);
    if (text) slot.textContent = `Data through ${text} · `;
  } catch (e) {
    // No date is better than a wrong one.
  }
}

// --- hints on terms ----------------------------------------------------------

// An underlined term explains itself. Pointing at it, tabbing to it or
// tapping it shows the explanation in one small box; Escape, tabbing away or
// tapping elsewhere closes it. Screen readers get the same words as the
// term's description, whether or not the box is showing.
function setUpTerms() {
  const terms = [...document.querySelectorAll(".term[data-tip]")];
  if (!terms.length) return;

  const bubble = document.createElement("div");
  bubble.className = "term-tip";
  bubble.hidden = true;
  document.body.append(bubble);
  let shown = null;

  const place = term => {
    const box = term.getBoundingClientRect();
    const margin = 8;
    bubble.style.maxWidth = `${Math.min(320, innerWidth - 2 * margin)}px`;
    bubble.style.left = "0px";
    bubble.style.top = "0px";
    const size = bubble.getBoundingClientRect();
    const left = Math.max(margin, Math.min(box.left, innerWidth - size.width - margin));
    const below = box.bottom + 6;
    const top = below + size.height > innerHeight - margin && box.top - 6 - size.height > margin
      ? box.top - 6 - size.height
      : below;
    bubble.style.left = `${left + scrollX}px`;
    bubble.style.top = `${top + scrollY}px`;
  };
  const show = term => {
    shown = term;
    bubble.textContent = term.dataset.tip;
    bubble.hidden = false;
    term.setAttribute("aria-expanded", "true");
    place(term);
  };
  const hide = () => {
    if (!shown) return;
    shown.setAttribute("aria-expanded", "false");
    shown = null;
    bubble.hidden = true;
  };

  terms.forEach((term, i) => {
    const note = document.createElement("span");
    note.id = `term-note-${i}`;
    note.className = "visually-hidden";
    note.textContent = term.dataset.tip;
    term.after(note);
    term.setAttribute("role", "button");
    term.setAttribute("tabindex", "0");
    term.setAttribute("aria-describedby", note.id);
    term.setAttribute("aria-expanded", "false");
    term.addEventListener("mouseenter", () => show(term));
    term.addEventListener("mouseleave", ev => {
      // Moving onto the box itself keeps it open, so its text can be read
      // and selected.
      if (ev.relatedTarget !== bubble) hide();
    });
    term.addEventListener("focus", () => show(term));
    term.addEventListener("blur", hide);
    term.addEventListener("click", () => (shown === term && !term.matches(":hover") ? hide() : show(term)));
    term.addEventListener("keydown", ev => {
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        if (shown === term) hide();
        else show(term);
      }
    });
  });
  bubble.addEventListener("mouseleave", hide);
  document.addEventListener("keydown", ev => {
    if (ev.key === "Escape") hide();
  });
  document.addEventListener("pointerdown", ev => {
    if (shown && !shown.contains(ev.target) && !bubble.contains(ev.target)) hide();
  });
  addEventListener("resize", () => shown && place(shown));
}

// --- printing ----------------------------------------------------------------

// A printed page cannot be unfolded, so open every folded section first and
// put each back as it was afterwards.
function setUpPrinting() {
  let opened = [];
  addEventListener("beforeprint", () => {
    opened = [...document.querySelectorAll("details:not([open])")];
    for (const d of opened) d.open = true;
  });
  addEventListener("afterprint", () => {
    for (const d of opened) d.open = false;
    opened = [];
  });
}

// --- copying code -------------------------------------------------------------

// Each block of example code gets a button that copies it.
function setUpCopying() {
  for (const block of document.querySelectorAll(".copyable pre")) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy";
    button.textContent = "Copy";
    button.setAttribute("aria-label", "Copy this code");
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(block.innerText);
        button.textContent = "Copied";
      } catch (e) {
        button.textContent = "Select and copy by hand";
      }
      setTimeout(() => { button.textContent = "Copy"; }, 2000);
    });
    block.parentElement.insertBefore(button, block);
  }
}

function start() {
  showDataThrough();
  setUpTerms();
  setUpPrinting();
  setUpCopying();
  const badge = document.getElementById("live");
  if (badge) watchLiveStatus(badge);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
else start();
