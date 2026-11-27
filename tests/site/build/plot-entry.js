// The chart library the site's pages load (site/vendor/plot-0.6.11/plot.js):
// the parts of Observable Plot 0.6.11 the pages call, and of d3 7.8.5 what
// those parts and the pages need, so a page loads far less than all of Plot
// and all of d3. Built by make-plot.mjs; add a function here before a page
// uses it (scripts/check_site.py fails a page that calls one not listed).
import {
  barX, barY, dot, lineY, plot, pointer, pointerX, pointerY, rectX, rectY, ruleX, ruleY,
  stackX, text, tip,
} from "@observablehq/plot";
import {autoType, csv} from "d3";

export const Plot = {
  barX, barY, dot, lineY, plot, pointer, pointerX, pointerY, rectX, rectY, ruleX, ruleY,
  stackX, text, tip,
};
export const d3 = {autoType, csv};
