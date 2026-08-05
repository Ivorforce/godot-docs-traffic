/* Godot documentation traffic — page logic.

   All data arrives in one generated data.js (window.DATA), so the page opens
   straight from disk with no server. State (corpus, section, filter, footprint
   mode) mirrors into location.hash.

   Pageviews are the only traffic metric anywhere in this file. Per-visitor and
   per-session figures are not reliable on this analytics setup (see the README)
   and never reach the page. */

"use strict";

const D = window.DATA;
const DOCS = D.docs;
const PREFIX = DOCS.prefix;

// Row layout emitted by scripts/aggregate.py. LINES is lines added plus lines
// deleted over the churn window; NET is added minus deleted, so a page whose
// lines and net are equal only grew, and one whose net is near zero was
// rewritten at its own length.
//
// PREV — the previous month's pageviews — is emitted and read by nothing. It
// was the table's month-on-month column, which said too little over two months
// to keep (see COLUMNS). It stays in the payload at one integer a row because
// it is where a trend would start, and a trend wants the other eleven months
// the raw data holds as well.
const PATH = 0, PV = 1, PREV = 2, WORDS = 3, EDITED = 4, I18N = 5, GENERATED = 6,
      LINES = 7, NET = 8, LENGTH = 9;

// The per-page monthly series behind the churn grid, and the months it covers.
const CHURN = DOCS.churn;

const state = { section: "", corpus: "manual", filter: "", footprint: "words" };
const charts = {};

const COLORS = {
  views: "#0969da",
  pages: "#8c959f",
  line: "#0969da",
  muted: "#8c959f",
  grid: "rgba(208,215,222,.6)",
};

/* A blue-to-amber ramp: the two families stay distinguishable in every colour
   vision type, and the steps inside each family differ in lightness, so the
   order survives too. Amber is the end to act on, as everywhere else here bar
   one: the churn grid's split bar is signed rather than ordered, and takes
   green and red for it. Every scale that ranks things stays in these families.

   Turnover is lines changed over the churn window against the page's length
   now, the same measure the churn grid shades by, so the two agree about what
   a busy page is. It is the one figure tested here that says something the
   position does not: against pageviews it measures −0.09 and against words
   0.01, where colouring by month-on-month change or by translation share both
   leaned on the horizontal axis.

   The first threshold is the lower quartile rather than zero exactly. Whether
   a quiet page received one typo fix in two years is an accident of who walked
   past it, and splitting there put only 3 pages in the amber bin inside the
   long-and-seldom-viewed corner, where the quartile puts 17 — a list someone can
   work through. The last is nameable rather than fitted: a page changed by its
   whole length again was rewritten, and that is the top eighth. */
const CHURN_BINS = [
  { max: 0.05, label: "barely touched (under 0.05× its length)", color: "#d4a72c" },
  { max: 0.30, label: "lightly worked (0.05 to 0.3×)", color: "#86b6ef" },
  { max: 1.00, label: "reworked (0.3 to 1×)", color: "#2a78d6" },
  { max: Infinity, label: "rewritten over (1× its length or more)", color: "#0d366b" },
];

/* One entry per getting-started tutorial, assigned in the order the docs list
   them. Two blues then two ambers, from the same families as the scales above,
   so the four stay distinguishable in every colour vision type. Each carries a
   marker shape as well, which is what keeps the lines apart in print, at a
   glance, and for a reader who cannot use the colours at all. Four is not a
   fixed count — the list wraps if the docs ever add a fifth. */
/* The editions stack. The current docs take the blue every other pageview
   figure here is drawn in, `latest` the same family a step lighter. Pinned
   releases — the editions this report leaves out — are the amber family, newest
   first, so a release darkens as it ages down the stack. */
const EDITION_COLORS = { stable: COLORS.views, latest: "#86b6ef",
                         other: COLORS.muted };
const EDITION_RAMP = ["#d4a72c", "#b0821c", "#7d4e00", "#4a2f00"];

const FUNNEL_STYLES = [
  { color: "#0d366b", point: "circle" },
  { color: "#2a78d6", point: "triangle" },
  { color: "#d4a72c", point: "rect" },
  { color: "#7d4e00", point: "rectRot" },
];

// ---- state <-> hash ---------------------------------------------------------

// "1" is what the two-way toggle wrote when the only choice was whether the
// class reference counted, and links carrying it are already out in the world.
function corpusOf(params) {
  const c = params.get("c");
  if (c === "all" || c === "1") return "all";
  return c === "classes" ? "classes" : "manual";
}

// Every key is assigned, absent or not: this also runs on popstate, where a key
// missing from the entry being restored means "back to the default" rather than
// "keep whatever is on screen".
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  state.section = p.get("s") || "";
  state.corpus = corpusOf(p);
  state.filter = p.get("q") || "";
  state.footprint = p.get("f") === "pages" ? "pages" : "words";
}

/* The hash for a state, which is also the hash for a link to it: a row that
   drills into a section is written as an anchor carrying this rather than as a
   click handler, so it can be opened in a second tab, copied, and followed by
   anything that reads links. Taken from a state object rather than from the
   live one so a link can name a view that is not the one on screen. */
function hashFor(next) {
  const p = new URLSearchParams();
  if (next.section) p.set("s", next.section);
  if (next.corpus !== "manual") p.set("c", next.corpus);
  if (next.filter) p.set("q", next.filter);
  if (next.footprint !== "words") p.set("f", next.footprint);
  const q = p.toString();
  return q ? "#" + q : "";
}

function writeHash() {
  const q = hashFor(state).slice(1);
  // Changing scope is a navigation and Back should undo it; typing in the filter
  // or flipping a chart mode only rewrites the current entry. Comparing against
  // the URL rather than against previous state also makes this correct on
  // popstate, where the URL already holds the state being restored and so no
  // entry is added.
  const cur = new URLSearchParams(location.hash.slice(1));
  const moved = (cur.get("s") || "") !== state.section ||
                corpusOf(cur) !== state.corpus;
  const url = q ? "#" + q : location.pathname;
  if (moved) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
}

// ---- links out --------------------------------------------------------------

const SITE = "https://docs.godotengine.org";
const REPO = "https://github.com/godotengine/godot-docs";

/* URL -> the source file behind it, inverting scripts/inventory.py's url_for.
   The only place that mapping is written twice, so a test walks every page in
   data/inventory.json against it rather than leaving 1,590 links to be spot
   checked by hand.

   It holds for a page that has since been removed as well, which is what the
   removed-pages table links by: git resolves a path that no longer exists, so
   the history of one ends in the commit that deleted it. */
function srcPath(url) {
  const rest = url.slice(PREFIX.length);
  return rest ? rest.replace(/\.html$/, ".rst") : "index.rst";
}

// Pinned to the docs commit every figure on this page was taken from, so the
// file that opens is the one the word count and the length were read off — not
// whatever the branch holds by the time somebody follows the link.
function sourceUrl(url) {
  return `${REPO}/blob/${DOCS.commit}/${srcPath(url)}`;
}

// The commits behind one page. Not narrowed to the churn window: GitHub's date
// filter drops the path scope, and the window is the newest two years of what
// this shows anyway.
function historyUrl(url) {
  return `${REPO}/commits/${DOCS.commit}/${srcPath(url)}`;
}

function link(href, text, title) {
  const a = document.createElement("a");
  a.href = href;
  a.textContent = text;
  if (title) a.title = title;
  return a;
}

/* A link drawn as the thing it is rather than as a link: a figure in a column
   of figures, a row name in a grid whose blue already means something else.
   Colour arrives on hover, where it says the same thing without spending the
   page's one accent on every cell. */
function quietLink(href, text, title) {
  const a = link(href, text, title);
  a.className = "quiet";
  return a;
}

// ---- selection --------------------------------------------------------------

function docPath(row) {
  return DOCS.paths[row[PATH]].slice(PREFIX.length);
}

/* Where the class reference sits, read off the pages rather than written in:
   inventory.py decides what counts as generated, and if it ever names a second
   directory this falls back to the docs root, so that corpus opens on those
   directories instead of on one arbitrary half of itself. */
const GENERATED_BASE = (() => {
  const tops = new Set();
  for (const row of DOCS.rows) {
    if (!row[GENERATED]) continue;
    const path = docPath(row);
    const cut = path.indexOf("/");
    tops.add(cut === -1 ? "" : path.slice(0, cut));
  }
  const [only] = tops;
  return tops.size === 1 && only ? only + "/" : "";
})();

/* What the path bar's first entries choose between. The class reference is
   1,079 generated pages against 511 hand-written ones: left in, it decides the
   shape of every page-count comparison on the page, so it is opt-in everywhere
   rather than only in the chart that motivated it.

   It is also offered on its own, because it is a different thing to maintain
   rather than a large section of the manual — synced from the engine's source,
   so the questions worth asking of it are about which classes people read and
   which the engine keeps moving, and every one of those is answered by a chart
   the manual crowds out. On its own it is rooted at classes/, since a corpus
   whose top level is a single directory would draw one bar at 100%.

   `tile` is the corpus named short enough for a stat tile, and with "the" in
   front of it, in the sentences this page generates — all of which said "the
   manual" when the manual was the only thing they could be about. */
const CORPORA = {
  all: {
    label: "Full Manual + Class Reference", base: "", has: () => true,
    tile: "English stable docs", pages: "including class reference",
  },
  manual: {
    label: "Full Manual", base: "", has: (r) => !r[GENERATED],
    tile: "hand-written manual", pages: "hand-written",
  },
  classes: {
    label: "Class Reference", base: GENERATED_BASE, has: (r) => !!r[GENERATED],
    tile: "class reference", pages: "generated from the engine's source",
  },
};

function corpus() {
  return CORPORA[state.corpus];
}

// A page's path within the corpus in view. The class reference is rooted at
// classes/, so its pages come out named as the reference names them rather
// than as one more directory of the manual.
function rel(row) {
  return docPath(row).slice(corpus().base.length);
}

function visibleRows() {
  const { has } = corpus();
  return DOCS.rows.filter((r) => has(r));
}

function inSection(row, section) {
  if (!section) return true;
  const r = rel(row);
  return r === section || r.startsWith(section + "/");
}

function sectionRows() {
  return visibleRows().filter((r) => inSection(r, state.section));
}

function filteredRows() {
  const q = state.filter.trim().toLowerCase();
  const rows = sectionRows();
  return q ? rows.filter((r) => DOCS.paths[r[PATH]].toLowerCase().includes(q)) : rows;
}

/* Children of the current section: sub-directories aggregated, pages sitting
   directly at this level kept as themselves. Both are drawn — a level whose
   traffic is mostly its own index page should look that way.

   `periods` is the churn series summed the same way, so the grid can follow the
   path bar off the same walk the section bars use. */
function childrenOf(section) {
  const base = section ? section + "/" : "";
  const out = new Map();
  for (const row of visibleRows()) {
    const r = rel(row);
    if (section && !r.startsWith(base)) continue;
    const rest = r.slice(base.length);
    // The manual's own landing page has nothing after the prefix. It is the
    // second most-viewed page there is, so dropping it silently cost the
    // pageview bars a tenth of their total while leaving the word bars intact.
    const cut = rest ? rest.indexOf("/") : -1;
    const key = !rest ? "(landing page)" : cut === -1 ? rest : rest.slice(0, cut);
    const entry = out.get(key) ||
      // `url` is the page itself where a child is one — a directory has no page
      // to point at, and only the grid's row names use it. A file name and a
      // directory name at the same level cannot collide, since one of them ends
      // in .html, so the first row to make the entry is the only one.
      { key, url: cut === -1 ? DOCS.paths[row[PATH]] : null,
        pv: 0, pages: 0, words: 0, length: 0, churn: 0, net: 0,
        periods: CHURN.periods.map(() => 0), dir: cut !== -1 };
    entry.pv += row[PV];
    entry.pages += 1;
    entry.words += row[WORDS];
    entry.length += row[LENGTH];
    entry.churn += row[LINES];
    entry.net += row[NET];
    const series = CHURN.series[row[PATH]];
    for (const period in series) entry.periods[period] += series[period];
    entry.dir = entry.dir || cut !== -1;
    out.set(key, entry);
  }
  return [...out.values()].sort((a, b) => b.pv - a.pv);
}

// ---- formatting -------------------------------------------------------------

const nf = new Intl.NumberFormat("en-US");
const num = (v) => nf.format(Math.round(v));

function pct(v, total) {
  if (!total) return "0%";
  const p = (100 * v) / total;
  return (p < 1 ? p.toFixed(1) : Math.round(p)) + "%";
}

/* Added against deleted, drawn out from the point where the two balance: right
   for something that grew, left for something that shrank, as far as the work
   tipped. Nothing is drawn for a thing that was not edited at all — a bar at
   the balance point means added and deleted came out equal, which is the
   opposite reading.

   Shared by the churn grid and the pages table, which ask the same question of
   a section and of a page, and would otherwise both answer it with two columns
   of figures and a division left to the reader. The figures stay in the title
   at both call sites, in each one's own words. */
function splitBar(net, churn) {
  if (!churn) return null;
  const bar = document.createElement("span");
  // 0 for something reworked at its own length, ±1 for one that only added or
  // only removed. Guarded, though net can only exceed churn if the two
  // disagree about what they counted.
  const tilt = Math.max(-1, Math.min(1, net / churn));
  bar.className = tilt < 0 ? "split down" : "split";
  const fill = document.createElement("i");
  fill.style.width = `${(50 * Math.abs(tilt)).toFixed(1)}%`;
  fill.style[tilt < 0 ? "right" : "left"] = "50%";
  bar.appendChild(fill);
  return bar;
}

/* 24,229 -> "24k". Only the churn grid uses this: ten columns of thousands
   separators do not fit its width, and the grid is read for shape and order of
   magnitude, with the exact figure a hover away and the table below carrying it
   in full. One decimal below 10k, where the difference between 1,279 and 4,926
   is worth a character. */
function compact(v) {
  if (v < 1000) return String(v);
  if (v < 10000) return (v / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return Math.round(v / 1000) + "k";
}

function ageLabel(ts) {
  if (!ts) return "unknown";
  const d = new Date(ts * 1000);
  return d.toISOString().slice(0, 7);
}

/* Lines changed over the churn window against the page's length now. Dividing
   by the length today rather than the length at the time is the same choice the
   churn grid makes: one divisor per page keeps a rewrite and a doubling in
   length from reading alike. */
function turnover(row) {
  return (row[LINES] || 0) / Math.max(row[LENGTH], 1);
}

function binFor(row) {
  return CHURN_BINS.find((b) => turnover(row) <= b.max);
}

function median(values) {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

// ---- chart defaults ---------------------------------------------------------

const BASE = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
};

/* Draws the median crosshairs and the corner labels on the scatter. An inline
   plugin rather than chartjs-plugin-annotation: one dependency is enough, and
   this needs twenty lines. */
const crosshairs = {
  id: "crosshairs",
  afterDatasetsDraw(chart, _args, opts) {
    if (!opts || !opts.x || !opts.y) return;
    const { ctx, chartArea: area, scales } = chart;
    const px = scales.x.getPixelForValue(opts.x);
    const py = scales.y.getPixelForValue(opts.y);
    ctx.save();
    ctx.strokeStyle = "rgba(87,96,106,.45)";
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(px, area.top); ctx.lineTo(px, area.bottom);
    ctx.moveTo(area.left, py); ctx.lineTo(area.right, py);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(87,96,106,.85)";
    ctx.font = "11px -apple-system, system-ui, sans-serif";
    const pad = 6;
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText("long · seldom viewed", area.left + pad, area.top + pad);
    ctx.textAlign = "right";
    ctx.fillText("long · widely viewed", area.right - pad, area.top + pad);
    ctx.textBaseline = "bottom";
    ctx.fillText("short · widely viewed", area.right - pad, area.bottom - pad);
    ctx.textAlign = "left";
    ctx.fillText("short · seldom viewed", area.left + pad, area.bottom - pad);
    ctx.restore();
  },
};

// ---- charts -----------------------------------------------------------------

function buildSections() {
  charts.sections = new Chart(document.getElementById("chart-sections"), {
    type: "bar",
    data: { labels: [], datasets: [
      // Each label carries its own denominator: the two series are percentages
      // of different totals, and an axis marked only "%" invites reading the
      // gap between them as a single quantity.
      { label: "pageviews — % of all pageviews", backgroundColor: COLORS.views,
        data: [] },
      { label: "size — % of all words", backgroundColor: COLORS.pages, data: [] },
    ]},
    options: Object.assign({}, BASE, {
      indexAxis: "y",
      // The whole row raises the tooltip, not the bar. Sorted by pageviews, the
      // sections worth asking about are the short bars at the bottom, and their
      // counts were the hardest to reach when the rectangle was the target.
      // axis: "y" because the bars run sideways — the default "x" would pick the
      // row whose bar happens to end nearest the pointer, anywhere on the canvas.
      interaction: { mode: "index", intersect: false, axis: "y" },
      scales: {
        x: { beginAtZero: true, ticks: { callback: (v) => v + "%" },
             grid: { color: COLORS.grid } },
        y: { grid: { display: false } },
      },
      plugins: {
        legend: { labels: { boxWidth: 12, font: { size: 11 } } },
        // All three magnitudes live here, so switching the second bar never
        // hides a number — it only changes which one is drawn to scale.
        tooltip: { callbacks: { afterBody(items) {
          const c = charts.sections.$children[items[0].dataIndex];
          const lines = [
            `${num(c.pv)} pageviews`,
            `${num(c.words)} words in ${num(c.pages)} page${c.pages === 1 ? "" : "s"}` +
              (c.pages ? ` (${num(c.words / c.pages)} each)` : ""),
          ];
          // The ratio the two bars ask the reader to work out, given directly.
          if (c.words) {
            lines.push(`${num((1000 * c.pv) / c.words)} pageviews per 1,000 words`);
          }
          return lines.join("\n");
        } } },
      },
    }),
  });
}

function buildScatter() {
  charts.scatter = new Chart(document.getElementById("chart-scatter"), {
    type: "scatter",
    data: { datasets: [] },
    options: Object.assign({}, BASE, {
      parsing: false,
      scales: {
        x: { type: "logarithmic", title: { display: true, text: "pageviews" },
             grid: { color: COLORS.grid },
             ticks: { callback: (v) => {
               const l = Math.log10(v);
               return l === Math.floor(l) ? num(v) : "";
             } } },
        y: { type: "logarithmic", title: { display: true, text: "words in the source file" },
             grid: { color: COLORS.grid },
             ticks: { callback: (v) => {
               const l = Math.log10(v);
               return l === Math.floor(l) ? num(v) : "";
             } } },
      },
      plugins: {
        legend: { labels: { boxWidth: 8, usePointStyle: true, font: { size: 11 } } },
        crosshairs: {},
        tooltip: { callbacks: {
          // The colouring is a ratio, so both sides of it are given here: the
          // bin a point landed in should not have to be trusted on the strength
          // of a legend key alone. Translation traffic rides along because it
          // is the other thing worth knowing before editing one named page,
          // even though the chart no longer colours by it.
          label(item) {
            const r = item.raw;
            return [r.short,
                    `${num(r.pv)} pageviews · ${num(r.words)} words`,
                    r.lines === 0
                      ? "untouched over the window"
                      : `${num(r.lines)} line${r.lines === 1 ? "" : "s"} changed · ` +
                        `${r.turnover.toFixed(2)}× its ${num(r.length)} lines`,
                    // The count, not the count over the English figure. Both
                    // left the table, for different reasons: the count because
                    // it ranks pages almost exactly as pageviews already does,
                    // the share because it measures 0.067 against the
                    // translated coverage it was taken to stand for. The first
                    // is redundant in a column and a plain fact on a hover,
                    // where nothing is being ranked. The second is not a fact
                    // about the page anywhere, so it is nowhere.
                    `translations ${num(r.i18n)}`,
                    // The date is kept here, where it is context for a page
                    // already chosen rather than an order to choose by. It was
                    // cut from the table for the sorting it invited.
                    `last edit ${r.age}`];
          },
        } },
      },
      onClick(evt, els) {
        if (!els.length) return;
        const point = charts.scatter.data.datasets[els[0].datasetIndex]
                        .data[els[0].index];
        state.filter = point.label;
        render();
        document.getElementById("pages-table").scrollIntoView({ behavior: "smooth" });
      },
    }),
    plugins: [crosshairs],
  });
}

function buildFunnel() {
  charts.funnel = new Chart(document.getElementById("chart-funnel"), {
    type: "line",
    data: { labels: [], datasets: [] },
    options: Object.assign({}, BASE, {
      // Points, not bars or the nearest column: the question here is always
      // about one page, and four series stacked at the same step would answer
      // with four pages the reader did not ask about.
      interaction: { mode: "nearest", intersect: false },
      scales: {
        x: { title: { display: true, text: "position in the tutorial" },
             grid: { display: false }, ticks: { font: { size: 10 } } },
        y: { beginAtZero: true, grid: { color: COLORS.grid },
             title: { display: true, text: "% of the tutorial's landing page" },
             ticks: { callback: (v) => v + "%" } },
      },
      plugins: {
        legend: { labels: { boxWidth: 8, usePointStyle: true, font: { size: 11 } } },
        tooltip: { callbacks: {
          // The share is what is drawn and the count is what it was computed
          // from; a percentage of an unstated base is the one number on this
          // chart a reader could not check.
          label(item) {
            const p = charts.funnel.data.datasets[item.datasetIndex].$pages[item.dataIndex];
            return `${p.short} — ${num(p.pv)} pageviews, ` +
                   `${Math.round(p.share)}% of the landing page`;
          },
        } },
      },
    }),
  });
}

/* English reading by edition, month by month, stacked. The one figure here
   drawn from more than one month (see README, "Which edition people read").
   Counts rather than shares: the flat total is half of what the chart says, and
   a 100% stack would hide it by construction. */
function buildEditions() {
  charts.editions = new Chart(document.getElementById("chart-editions"), {
    type: "bar",
    data: { labels: [], datasets: [] },
    options: Object.assign({}, BASE, {
      // The whole column, because the question is what a month was made of and
      // the thin bands on top are the ones worth reading.
      interaction: { mode: "index", intersect: false },
      /* Bars that touch, so each band reads as one layer across time the way a
         stacked area does, while every month stays a closed block. An area
         would put its vertices at month centres and interpolate a third of a
         year between two of them; at 36 points or so that stops mattering and
         this should become one (see README). */
      datasets: { bar: { barPercentage: 1, categoryPercentage: 1 } },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { font: { size: 10 } } },
        y: { stacked: true, beginAtZero: true, grid: { color: COLORS.grid },
             ticks: { callback: (v) => num(v) } },
      },
      plugins: {
        legend: { labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: {
          label(item) {
            const total = item.chart.data.datasets
              .reduce((a, d) => a + d.data[item.dataIndex], 0);
            const pv = item.parsed.y;
            return `${item.dataset.label} — ${num(pv)} (${pct(pv, total)})`;
          },
          // The sum the stack draws, given rather than left to be added up: it
          // is the thing that does not move, which is hard to see in a stack.
          footer(items) {
            const total = items[0].chart.data.datasets
              .reduce((a, d) => a + d.data[items[0].dataIndex], 0);
            return `${num(total)} pageviews in the month`;
          },
        } },
      },
    }),
  });
}

// ---- rendering --------------------------------------------------------------

function renderStats() {
  const rows = visibleRows();
  const total = rows.reduce((a, r) => a + r[PV], 0);
  const sorted = [...rows].sort((a, b) => b[PV] - a[PV]);
  let running = 0, half = 0;
  for (const r of sorted) {
    running += r[PV];
    half += 1;
    if (running >= total / 2) break;
  }
  const quiet = rows.filter((r) => r[PV] < 100).length;
  /* A tile's second line says what its figure is of, and nothing else. Both
     editions exclusions used to ride here on the tile they are missing from,
     which made one subtitle four times the width of its neighbours and pushed
     the row out of shape. They are named in the caveats instead, where the rest
     of what this page does not count already is. */
  const stats = [
    ["pageviews", num(total), corpus().tile],
    ["pages", num(rows.length), corpus().pages],
    ["carry half the pageviews", num(half), `${pct(half, rows.length)} of pages`],
    ["under 100 views", num(quiet), `${pct(quiet, rows.length)} of pages`],
    ["viewed in translation", num(DOCS.i18nViews), "all other languages"],
  ];
  const box = document.getElementById("stats");
  box.replaceChildren(...stats.map(([label, value, sub]) => {
    const div = document.createElement("div");
    const b = document.createElement("b");
    b.textContent = value;
    div.append(b, label);
    if (sub) {
      const small = document.createElement("div");
      small.textContent = sub;
      small.style.fontSize = ".8em";
      small.style.opacity = ".8";
      div.appendChild(small);
    }
    return div;
  }));
}

/* The path bar. Ancestors are plain links that move up; only the level you are
   on carries a menu, of the sub-sections inside it. That is enough to reach
   anywhere: changing a level means clicking the level above it and picking
   again, the same move a column view makes. One control rather than one per
   crumb also lets it be drawn as a control — a caret small enough to sit
   between crumbs is not something a reader finds.

   Menus list directories only. Pages sitting at a level are not places you can
   go, and the chart below already shows them alongside their siblings. */

let openMenu = null;

function closeMenu() {
  if (!openMenu) return;
  document.removeEventListener("click", openMenu.onOutside);
  openMenu.menu.remove();
  openMenu.button.setAttribute("aria-expanded", "false");
  openMenu = null;
}

/* The corpus entries sit at the top of the root menu rather than in a control
   of their own: the class reference is 1,079 generated pages under classes/ and
   nothing else lives there, so "include it" and "look at this part of the
   manual" are the same kind of choice and belong in the same list. */
function menuItems(section) {
  const items = [];
  const children = childrenOf(section);
  const total = children.reduce((a, c) => a + c.pv, 0);
  for (const child of children) {
    if (!child.dir) continue;
    const target = section ? section + "/" + child.key : child.key;
    items.push({ label: child.key, note: pct(child.pv, total),
                 run: () => { state.section = target; } });
  }
  if (section) return items;

  const corpora = Object.keys(CORPORA).map((key) => ({
    label: CORPORA[key].label,
    checked: state.corpus === key,
    // The section goes with it: a path inside one corpus names nothing in
    // another, and the class reference has no sections to land in at all.
    run: () => { state.corpus = key; state.section = ""; },
  }));
  // The rule only earns its place if something follows it. The class reference
  // is a flat list of pages, so its root menu is the three corpora and nothing
  // else.
  return items.length ? [...corpora, { separator: true }, ...items] : corpora;
}

function buildMenu(button, heading, items) {
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.setAttribute("role", "menu");
  // The percentages need their denominator said out loud: they are shares of the
  // level the menu was opened from, not of the manual.
  const head = document.createElement("div");
  head.className = "menu-head";
  head.textContent = heading;
  menu.appendChild(head);

  const entries = [];
  for (const item of items) {
    if (item.separator) {
      const hr = document.createElement("div");
      hr.className = "menu-sep";
      menu.appendChild(hr);
      continue;
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "menu-item";
    btn.setAttribute("role", item.checked === undefined ? "menuitem" : "menuitemradio");
    if (item.checked !== undefined) btn.setAttribute("aria-checked", String(item.checked));
    const label = document.createElement("span");
    label.className = "menu-label";
    label.textContent = item.label;
    btn.appendChild(label);
    if (item.note) {
      const note = document.createElement("span");
      note.className = "menu-note";
      note.textContent = item.note;
      btn.appendChild(note);
    }
    btn.addEventListener("click", () => { closeMenu(); item.run(); render(); });
    menu.appendChild(btn);
    entries.push(btn);
  }

  const move = (from, step) => {
    const i = entries.indexOf(from);
    entries[(i + step + entries.length) % entries.length].focus();
  };
  menu.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); move(e.target, 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); move(e.target, -1); }
    else if (e.key === "Home") { e.preventDefault(); entries[0].focus(); }
    else if (e.key === "End") { e.preventDefault(); entries[entries.length - 1].focus(); }
    else if (e.key === "Escape" || e.key === "Tab") { closeMenu(); button.focus(); }
  });
  return { menu, entries };
}

function toggleMenu(button, wrap, section, label) {
  if (openMenu && openMenu.button === button) { closeMenu(); return; }
  closeMenu();
  const items = menuItems(section);
  // The heading says what the percentages are shares of, so it goes when there
  // are none: the class reference's root menu lists corpora and no sections.
  const heading = !items.some((i) => i.note)
    ? "what this report covers"
    : section ? `share of ${label} pageviews` : "share of all pageviews";
  const { menu, entries } = buildMenu(button, heading, items);
  wrap.appendChild(menu);
  button.setAttribute("aria-expanded", "true");
  const onOutside = (e) => {
    if (!menu.contains(e.target) && e.target !== button) closeMenu();
  };
  // Deferred by a tick: the click that opened the menu is still bubbling towards
  // the document and would otherwise close it again immediately.
  setTimeout(() => document.addEventListener("click", onOutside), 0);
  openMenu = { button, menu, onOutside };
  if (entries.length) entries[0].focus();
}

function renderCrumbs() {
  const box = document.getElementById("crumbs");
  closeMenu();
  box.replaceChildren();
  const parts = state.section ? state.section.split("/") : [];

  const add = (text, section, last) => {
    const wrap = document.createElement("span");
    wrap.className = "crumb";
    if (!last) {
      const jump = document.createElement("button");
      jump.type = "button";
      jump.className = "crumb-jump";
      jump.textContent = text;
      jump.addEventListener("click", () => { state.section = section; render(); });
      wrap.appendChild(jump);
    } else if (menuItems(section).some((i) => !i.separator)) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "crumb-current";
      btn.setAttribute("aria-haspopup", "menu");
      btn.setAttribute("aria-expanded", "false");
      const label = document.createElement("span");
      label.textContent = text;
      const caret = document.createElement("span");
      caret.className = "caret";
      caret.textContent = "▾";
      btn.append(label, caret);
      btn.addEventListener("click", () => toggleMenu(btn, wrap, section, text));
      wrap.appendChild(btn);
    } else {
      // A section with nothing inside it is a place, not a control.
      const b = document.createElement("b");
      b.textContent = text;
      wrap.appendChild(b);
    }
    box.appendChild(wrap);
  };

  add(corpus().label, "", parts.length === 0);
  parts.forEach((part, i) => {
    const sep = document.createElement("span");
    sep.className = "crumb-sep";
    sep.textContent = "/";
    box.appendChild(sep);
    add(part, parts.slice(0, i + 1).join("/"), i === parts.length - 1);
  });
}

/* One row per child, and the chart is read by comparing the pair of bars on a
   row, which needs the row to have height. A level can hold far more children
   than that allows: the class reference is 1,079 pages at one level, and drawn
   in full they are a smear a pixel high apiece. So the bars stop at the
   most-viewed and what the cap left out is printed underneath, the same as the
   churn grid does.

   Thirty is above every level the hand-written manual has — tutorials/ is the
   largest at 26 — so nothing there is cut today. Both bars stay shares of the
   whole level either way, so the ones drawn keep meaning what they mean and
   simply no longer sum to 100%. */
const SECTION_BARS = 30;

function renderSections() {
  const children = childrenOf(state.section);
  const shown = children.slice(0, SECTION_BARS);
  const rows = sectionRows();
  const totalPv = rows.reduce((a, r) => a + r[PV], 0) || 1;
  const byWords = state.footprint === "words";
  const size = (c) => (byWords ? c.words : c.pages);
  const totalSize = rows.reduce((a, r) => a + (byWords ? r[WORDS] : 1), 0) || 1;

  charts.sections.$children = shown;
  charts.sections.data.labels = shown.map((c) => c.key + (c.dir ? "/" : ""));
  charts.sections.data.datasets[0].data =
    shown.map((c) => Math.round((1000 * c.pv) / totalPv) / 10);
  charts.sections.data.datasets[1].data =
    shown.map((c) => Math.round((1000 * size(c)) / totalSize) / 10);
  charts.sections.data.datasets[1].label =
    byWords ? "size — % of all words" : "size — % of all pages";
  charts.sections.update();

  document.getElementById("sections-note").textContent =
    children.length > shown.length
      ? `The ${shown.length} most-viewed of ${num(children.length)} are shown. ` +
        `Both bars remain shares of the level as a whole, so what is drawn no ` +
        `longer adds up to it.`
      : "";
}

function renderScatter() {
  const rows = sectionRows();
  const groups = new Map(CHURN_BINS.map((b) => [b.label, []]));
  for (const row of rows) {
    groups.get(binFor(row).label).push({
      // A page with no pageviews at all would fall off a log axis; plotting it
      // just below 1 keeps it visible without claiming it was read.
      x: Math.max(row[PV], 0.5),
      y: Math.max(row[WORDS], 1),
      pv: row[PV], words: row[WORDS], i18n: row[I18N],
      lines: row[LINES] || 0,
      length: row[LENGTH],
      turnover: turnover(row),
      age: ageLabel(row[EDITED]),
      // The full URL is what the table filters on, so it is kept; the tooltip
      // shows the part that differs between points, since every page here is
      // under the same prefix and repeating it costs a line of width.
      label: DOCS.paths[row[PATH]],
      short: rel(row) || "(landing page)",
    });
  }
  charts.scatter.data.datasets = CHURN_BINS.map((b) => ({
    label: b.label,
    // Ascending in x, which the chart is entitled to assume and cannot check:
    // `parsing: false` has Chart.js mark every dataset sorted, and it then
    // takes the x range from the first and last point alone. Fed in page order
    // that range is two arbitrary pages wide, and the pages outside it are
    // clipped away at the edge of the plot. Hover reads the same assumption
    // through a binary search, so unsorted points also answer with the wrong
    // page.
    data: groups.get(b.label).sort((p, q) => p.x - q.x),
    backgroundColor: b.color,
    pointRadius: 2.5,
    pointHoverRadius: 5,
  }));
  charts.scatter.options.plugins.crosshairs = {
    x: median(rows.map((r) => Math.max(r[PV], 0.5))),
    y: median(rows.map((r) => Math.max(r[WORDS], 1))),
  };
  charts.scatter.update();
}

// How lopsided the traffic is, in words rather than as a chart: at this
// concentration a cumulative curve is a vertical wall and then a flat line, and
// the numbers below are the whole of what it had to say.
function renderConcentration() {
  const pv = sectionRows().map((r) => r[PV]).sort((a, b) => b - a);
  const total = pv.reduce((a, v) => a + v, 0) || 1;
  let running = 0;
  const cumulative = pv.map((v) => (100 * (running += v)) / total);
  const rank = (target) => {
    const idx = cumulative.findIndex((v) => v >= target);
    return idx === -1 ? pv.length : idx + 1;
  };
  const quiet = pv.filter((v) => v < 100).length;
  const zero = pv.filter((v) => v === 0).length;
  const where = state.section
    ? `${PREFIX}${corpus().base}${state.section}/`
    : `the ${corpus().tile}`;
  let text =
    `Of ${num(pv.length)} pages in ${where}, the ${num(rank(50))} most-viewed carry ` +
    `half the pageviews, ${num(rank(80))} carry 80%, and ${num(rank(95))} carry 95% ` +
    `— the concentration usual for documentation, where most people arrive with ` +
    `one question.`;
  // Only worth saying where there is a quiet end to say it about; a small
  // section can have none, and the sentence would then argue with nobody.
  if (quiet > 0) {
    text += ` ${num(quiet)} pages were opened fewer than 100 times` +
            (zero === 1 ? `, and one not at all` :
             zero > 1 ? `, and ${num(zero)} not at all` : ``) +
            `. Low traffic is not low value: those are the pages a search engine ` +
            `sent someone to on the day they needed exactly that.`;
  }
  document.getElementById("concentration").textContent = text;
}

/* Folder name to tutorial name: "first_2d_game" -> "first 2D game". The docs
   carry a real title for each of these, but only in the reStructuredText the
   inventory does not keep, and the folder is what a restructure renames
   anyway. The digit case is fixed because "2d" reads as a typo. */
function tutorialLabel(landing) {
  return landing.slice(PREFIX.length + "getting_started/".length)
                .split("/")[0]
                .replace(/_/g, " ")
                .replace(/\b(\d)d\b/g, "$1D");
}

// The page within its tutorial: the folder is already the series name, and the
// "01." prefixes are already the x axis.
function stepLabel(url) {
  return url.split("/").pop().replace(/\.html$/, "")
            .replace(/^\d+\./, "").replace(/[-_]/g, " ");
}

function funnelSeries() {
  const byPath = new Map(DOCS.rows.map((r) => [DOCS.paths[r[PATH]], r[PV]]));
  return DOCS.funnel.map((group, i) => {
    const landing = byPath.get(group.landing) || 0;
    const pages = [group.landing, ...group.steps].map((url, step) => {
      const pv = byPath.get(url) || 0;
      return {
        url, pv,
        short: step ? stepLabel(url) : "landing page",
        // Against the landing page rather than against step one, so the first
        // segment of every line is the drop at the door. In both game
        // tutorials that is the largest single fall in the sequence.
        share: landing ? (100 * pv) / landing : 0,
      };
    });
    return Object.assign({ title: tutorialLabel(group.landing), landing, pages },
                         FUNNEL_STYLES[i % FUNNEL_STYLES.length]);
  });
}

function renderFunnel() {
  const series = funnelSeries();
  // The tutorials are 7 to 11 pages long. Chart.js aligns each dataset to the
  // shared label list by index, so the shorter lines simply stop early.
  const longest = Math.max(...series.map((s) => s.pages.length));
  charts.funnel.data.labels = Array.from(
    { length: longest }, (_, i) => (i ? String(i) : "landing"));
  charts.funnel.data.datasets = series.map((s) => ({
    label: s.title,
    data: s.pages.map((p) => p.share),
    $pages: s.pages,
    borderColor: s.color,
    backgroundColor: s.color,
    pointStyle: s.point,
    pointRadius: 4,
    pointHoverRadius: 7,
    borderWidth: 2,
    // Straight segments. A curve through these would invent readings between
    // steps, and there is nothing between step 3 and step 4.
    tension: 0,
  }));
  charts.funnel.update();
  renderFunnelNote(series);
}

/* The drop at the door, in figures. Asserted from the data rather than written
   into the caption: the sentence is only true while it is true, and a hardcoded
   one would go on claiming it after the next refresh moved a number. */
function renderFunnelNote(series) {
  const tallest = series.every((s) => s.pages.every((p) => p.share <= 100));
  const parts = series.map((s) =>
    `${s.title}, ${num(s.landing)} pageviews and ${Math.round(s.pages[1].share)}%`);
  document.getElementById("funnel-note").textContent =
    (tallest
      ? "Every tutorial's landing page is opened more than any page inside it. "
      : "") +
    `Landing page, then the share of it that reaches the first step: ` +
    parts.join("; ") + ".";
}

function monthLabel(month) {
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${names[Number(month.slice(5)) - 1]} '${month.slice(2, 4)}`;
}

/* The newest months the stack draws. The payload holds every month fetched and
   grows by one a month, so something has to bound the chart; two years is two
   release cycles. The cut is named in the note rather than left to be noticed. */
const EDITION_MONTHS = 24;

/* Months rather than quarters, unlike the churn grid, for two reasons the
   README sets out in figures: a quarter averages the two-month handover into a
   single edge, and a stacked count drawn from a part-period is short rather
   than partial, which reads as traffic collapsing. */
function editionWindow() {
  const start = Math.max(0, D.months.length - EDITION_MONTHS);
  return {
    start,
    months: D.months.slice(start),
    series: DOCS.editions.map(
      (e) => ({ edition: e.edition, views: e.views.slice(start) })),
  };
}

function renderEditions() {
  // Amber is assigned in the order the editions arrive, which aggregate.py
  // sorts newest release first, so the brightest band is the newest pinned one
  // and a release keeps its shade as it decays down the stack.
  const ramp = [...EDITION_RAMP];
  const win = editionWindow();
  charts.editions.data.labels = win.months.map(monthLabel);
  charts.editions.data.datasets = win.series.map((e) => ({
    label: e.edition === "other" ? "older releases" : "/en/" + e.edition + "/",
    data: e.views,
    backgroundColor: EDITION_COLORS[e.edition] || ramp.shift() || COLORS.muted,
  }));
  charts.editions.update();
  renderEditionsNote(win);
}

/* What the stack is for, in figures, and asserted from the data: a sentence
   written by hand would go on claiming a handover that has since finished. */
function renderEditionsNote(win) {
  const months = win.months;
  const last = months.length - 1;
  const totals = months.map((_, i) =>
    win.series.reduce((a, e) => a + e.views[i], 0));
  const stable = win.series.find((e) => e.edition === "stable");
  const pinned = win.series
    .filter((e) => /^\d/.test(e.edition))
    .map((e) => {
      const peak = e.views.indexOf(Math.max(...e.views));
      return { edition: e.edition, peak, views: e.views };
    })
    // The one with the longest history behind its peak is the only one whose
    // decay has finished inside the window; a release that peaked last month
    // has a tail nobody can measure yet.
    .sort((a, b) => a.peak - b.peak)[0];
  const parts = [];
  if (stable) {
    parts.push(
      `In ${months[last]}, ${pct(stable.views[last], totals[last])} of English ` +
      `pageviews were on the current docs and the rest on an edition this ` +
      `report excludes.`);
  }
  if (pinned && pinned.peak < last) {
    parts.push(
      `/en/${pinned.edition}/ peaked at ${pct(pinned.views[pinned.peak], totals[pinned.peak])} ` +
      `of the month in ${months[pinned.peak]} and is at ` +
      `${pct(pinned.views[last], totals[last])} now.`);
  }
  if (win.start) {
    parts.push(`The newest ${months.length} of ${D.months.length} stored ` +
               `months are drawn.`);
  }
  document.getElementById("editions-note").textContent = parts.join(" ");
}

/* Four columns: how much a page is viewed, what it costs to keep, and how much
   of it has moved lately. Which is the scatter above, read one page at a time
   — its two axes and its colouring — and it is what this report has to say
   about a page. Four of the eight this table used to carry were dropped, each
   for its own reason, and the reasons are kept because the columns were
   plausible.

   Month-on-month change was one figure against one other, over two months, and
   the report holds thirteen. Half the manual moved less than 8.5% and the page
   as a whole moved 5.7%, which no row subtracted, so most of the column was
   the site's own weather. What was left of the sort was small pages and new
   ones: of the thirty largest risers, twenty-three drew under 250 pageviews the
   month before, against a median of 566 across the table, and four of the top
   ten were community/asset_store/, which is a section being published rather
   than a page being viewed. A trend wants more than two points and belongs in a
   line, not a ratio.

   Translation traffic, as a count, ranked pages at 0.93 against pageviews — 42
   of the top 50 were the same pages in nearly the same order. As a share of the
   page's English pageviews it ranked them differently, and was cut anyway,
   because what it ranked them by is not a fact about the page: against the
   translated coverage in data/l10n.json it measures 0.067, so it does not say
   which pages the translations have reached or which ones an English rewrite
   would cost work. Its extremes are small denominators — the twenty highest
   shares have a median of 73 pageviews against 609 across the table, and no
   page above 3,000 pageviews reaches 1× at all. Coverage itself is the
   measure those readings wanted, and it is not in this payload; see the README.

   Last edit was cut for the reason it had already been cut from the scatter's
   colouring: it cannot be read in either direction. An old date is a page that
   is finished or a page that is forgotten, and nothing here separates them. A
   recent one is mostly a typo — 29% of these pages were last edited in a
   quarter that moved two lines or fewer, 60% in one that moved ten or fewer,
   and the median is six lines, 0.007× the page. Sorted newest first the column
   put about/introduction.html, at 37,096 pageviews, under a 2026-06 date it
   earned with a two-line change. At the old end it adds nothing either: the 60
   pages last edited before the window opened already read zero lines changed,
   and the date only says how much further back. Lines changed measures the same
   activity by size, which is the version that can be read.

   Lines changed and net were two figures that answered one question by
   division, which is the objection the churn grid's own total answered with a
   bar — so this column takes the same bar. */
const COLUMNS = [
  { key: "path", label: "page", get: (r) => DOCS.paths[r[PATH]] },
  { key: PV, get: (r) => r[PV], fmt: num },
  { key: WORDS, get: (r) => r[WORDS], fmt: num },
  { key: LINES, get: (r) => r[LINES] || 0, fmt: num },
];

const sortState = { col: 1, asc: false };

function renderTable() {
  const rows = [...filteredRows()];
  const col = COLUMNS[sortState.col];
  rows.sort((a, b) => {
    const va = col.get(a), vb = col.get(b);
    if (va === null) return 1;
    if (vb === null) return -1;
    const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
    return sortState.asc ? cmp : -cmp;
  });

  const tbody = document.querySelector("#pages-table tbody");
  tbody.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    const path = DOCS.paths[row[PATH]];

    const td0 = document.createElement("td");
    td0.className = "path";
    td0.appendChild(link(SITE + path, path.slice(PREFIX.length) || "(index)",
                         "Open the page"));
    tr.appendChild(td0);

    for (const c of COLUMNS.slice(1)) {
      const td = document.createElement("td");
      const v = c.get(row);
      const text = c.fmt ? c.fmt(v) : String(v);
      // Two of the three figures lead to where they came from: the source file
      // the words were counted in, and the commits the lines were counted from.
      // Which is the move this report exists to prompt — a page it says
      // something about is one somebody then has to open — and it costs no
      // column, since a figure that leads somewhere is still the figure.
      //
      // Pageviews link nowhere. Plausible's own dashboard is the only place
      // that number leads and it is not public, so the column stays plain
      // rather than sending a reader to a login page.
      if (c.key === WORDS) {
        td.appendChild(quietLink(sourceUrl(path), text,
                                 "The source file this count was taken from"));
      } else if (c.key === LINES) {
        // No title here: this cell's own hover carries the figures behind the
        // bar, and a title on the anchor would cover them with a sentence about
        // where the link goes. That sentence rides along at the end of them.
        td.appendChild(quietLink(historyUrl(path), text));
      } else {
        td.textContent = text;
      }
      // The one column drawn as a shape rather than read as a figure keeps its
      // figures behind it: the bar is what the eye compares down the column,
      // and the numbers are what a maintainer quotes.
      if (c.key === LINES) {
        const churn = row[LINES] || 0, net = row[NET] || 0;
        const added = (churn + net) / 2;
        const bar = splitBar(net, churn);
        if (bar) {
          td.className = "churn";
          td.appendChild(bar);
          td.title =
            `${num(churn)} lines changed over the window · ` +
            `${num(added)} added, ${num(churn - added)} deleted · ` +
            `net ${net < 0 ? "−" : "+"}${num(Math.abs(net))}, ` +
            `${Math.abs(net / Math.max(row[LENGTH], 1)).toFixed(2)}× the ` +
            `${num(row[LENGTH])} lines this page runs to now · ` +
            `opens the commits behind them`;
        } else {
          td.title = "not edited over the churn window · " +
                     "opens the commits behind this page";
        }
      }
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }

  document.querySelectorAll("#pages-table th").forEach((th, i) => {
    th.classList.toggle("sorted", i === sortState.col);
    th.classList.toggle("asc", i === sortState.col && sortState.asc);
  });
  document.getElementById("table-scope").textContent = `— ${num(rows.length)}`;
}

/* Lines changed per month for the children of the current section, drawn as a
   table rather than a canvas.

   Most of what this grid has to say is where the counts stop — something silent
   for a quarter, something that woke up last month. That makes the numbers the
   content, so they are printed as numbers and the tint sits over them as a
   second cue rather than the only one, which also keeps the empty cells legible
   where a scale bottoming out at white would not.

   Rows are capped: a level with a thousand children is the class reference, and
   nobody reads a thousand-row grid. What the cap left out is said underneath
   rather than silently dropped. */
const CHURN_ROWS = 25;

function renderChurn() {
  const children = childrenOf(state.section);
  const edited = children.filter((c) => c.churn > 0)
                         .sort((a, b) => b.churn - a.churn);
  const shown = edited.slice(0, CHURN_ROWS);
  // Shading runs to the most intensively changed cell on screen rather than to
  // a fixed "changed by its whole length". Sections almost never reach that
  // ceiling — the busiest quarter tutorials/ ever had turned over 17% of it —
  // so anchoring there spent the top three quarters of the scale on nothing and
  // squeezed every section into the palest end, which is the same washout as
  // shading the raw counts, arrived at from the other side.
  const top = Math.max(...shown.map(
    (c) => Math.max(...c.periods) / Math.max(c.length, 1)), 1e-9);
  // The manual's own balance, for the hover to put a row's split beside. Not
  // drawn on the bars: they diverge from the point where added and deleted are
  // equal, and a second reference an inch from the first is two marks to tell
  // apart in three-quarters of an inch.
  //
  // Taken over every visible row rather than over the section on screen, so
  // drilling in does not move it and two rows from two views can be compared.
  // The corpus does move it, and by almost nothing: 76.5% hand-written against
  // 76.8% with the generated pages in.
  const whole = visibleRows().reduce(
    (acc, r) => { acc.c += r[LINES]; acc.n += r[NET]; return acc; },
    { c: 0, n: 0 });
  const ref = whole.c ? (whole.c + whole.n) / 2 / whole.c : 0.5;
  const cell = (tag, text) => {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  };

  // Where a new year begins, taken from the months behind each column rather
  // than from its label. Drawn as a rule down the grid: the labels carry the
  // year already, but eight of them abbreviated to "Q1 '25" are read as one run
  // of quarters, and placing a cell in time meant counting columns back to the
  // header. The first column starts no year — the grid opens mid-year and a
  // rule down its left edge would be a border, not a boundary.
  const yearStart = CHURN.periods.map((_, i) =>
    i > 0 && CHURN.months[i][0].slice(0, 4) !== CHURN.months[i - 1][0].slice(0, 4));

  const head = document.createElement("tr");
  head.appendChild(cell("th", "page or section"));
  CHURN.periods.forEach((period, i) => {
    // The newest column stops at the docs commit rather than at the end of its
    // quarter, so it is short by however much of the period is still to come.
    // Marked in the header, because read as a full column it looks like work
    // falling off.
    const th = cell("th", period.label + (period.partial ? "*" : ""));
    if (period.partial) th.title = "part-period: ends at the docs commit";
    if (yearStart[i]) th.className = "year";
    head.appendChild(th);
  });
  const total = cell("th", "changed");
  total.className = "total";
  total.title = "lines changed over the whole window; the bar runs right for a " +
                "row that grew and left for one that shrank";
  head.appendChild(total);

  const body = document.createElement("tbody");
  for (const child of shown) {
    const name = child.key + (child.dir ? "/" : "");
    // Shaded against the row's own current length, so a full cell means "this
    // period changed as many lines as the thing is long" — rewritten, or
    // written. Anything past that saturates.
    //
    // A scale shared across raw line counts spent its whole range separating
    // tutorials/ from community/, which the numbers and the ordering already
    // say, and almost none on when a row was busy. Dividing by length fixes
    // that and keeps every cell comparable to every other, down a column as
    // well as across a row, which shading against each row's own peak would
    // have thrown away — under that, one 20-line edit to a dead section looks
    // exactly as hot as tutorials at 24,000.
    //
    // The denominator is the length today, not the length at the time. That is
    // the better choice rather than a concession: one divisor per row leaves
    // the row's shape untouched, where a moving one would distort the very
    // profile the row exists to show.
    const length = Math.max(child.length, 1);
    const tr = document.createElement("tr");
    const label = cell("th", "");
    label.scope = "row";
    // Every name in this column stays inside the report: a section opens it on
    // itself, the move the path bar's menu makes, and a page narrows the table
    // below to that page, the move a point on the scatter makes. A row of this
    // grid is a place in the report, and the links out of the report all sit in
    // one place — the table, where a page carries its own docs page, source
    // file and commits. Sending some rows here to GitHub and others to the docs
    // meant a column where the destination had to be guessed from the name.
    //
    // The column is narrow enough to clip a long page name, so the full one
    // stays in the title, which the anchor inherits.
    const target = state.section ? state.section + "/" + child.key : child.key;
    const view = child.dir ? { section: target } : { filter: child.url };
    label.title = child.dir
      ? `${name} — open the report on this section`
      : `${name} — show this page in the table below`;
    label.appendChild(
      quietLink(hashFor(Object.assign({}, state, view)) || "#", name));
    tr.appendChild(label);
    child.periods.forEach((lines, i) => {
      const td = cell("td", lines ? compact(lines) : "·");
      td.className = (lines ? "cell" : "cell empty") + (yearStart[i] ? " year" : "");
      td.title = lines
        ? `${num(lines)} lines changed in ${CHURN.periods[i].label} · ` +
          `${(lines / length).toFixed(2)}× the ${num(child.length)} lines ` +
          `${name} runs to now`
        : `${name} was not edited in ${CHURN.periods[i].label}`;
      if (lines) {
        // Square root, because the cells are heavy-tailed however they are
        // measured: across every level of the manual the darkest cell is a
        // median of 13x the typical one, so a linear ramp leaves the bulk of
        // the grid at a tenth of the range and unreadable. Root is gentler than
        // a log curve, which pushed the quiet cells so dark that absence — the
        // thing this grid is for — stopped being visible.
        const weight = Math.sqrt(Math.min(1, lines / length / top));
        td.style.background = `rgba(9,105,218,${(0.07 + 0.53 * weight).toFixed(3)})`;
      }
      tr.appendChild(td);
    });
    // The window total, which is also what the rows are ordered by, and the
    // added-against-deleted split drawn out from the point where the two
    // balance.
    //
    // The split was two columns of figures, which asked the reader to divide
    // one by the other to arrive at the only thing they say: whether a section
    // grew or was reworked at the length it already had. Both were whole-window
    // figures sitting at the end of a row of quarters, so they read as two more
    // periods, and they were the widest columns in the table.
    //
    // A bar filled from the left said it as a length instead, which is the
    // comparison the reader wanted, but it buried the sign: a row that came out
    // shorter is one whose fill stops before half way, and nothing on the bar
    // said where half way was. Drawing from the balance point puts the sign in
    // the direction, where it costs nothing to read and cannot be got wrong,
    // and leaves the length saying how far the work tipped. The figures stay a
    // hover away, where the rest of this grid keeps them too.
    //
    // The pages table below now draws the same bar for the same reason, off
    // splitBar, so a section and a page are read the same way.
    const added = (child.churn + child.net) / 2;
    const deleted = child.churn - added;
    const td = cell("td", compact(child.churn));
    td.className = "total";
    const bar = splitBar(child.net, child.churn);
    if (bar) td.appendChild(bar);
    // The net is the figure that needs a scale beside it: +500 lines is a
    // rewrite of a short page and a rounding error on tutorials/. It gets the
    // one the cells above already use — the row's length now — in the same
    // words, so the two read as one vocabulary rather than two.
    td.title = `${num(child.churn)} lines changed over the window · ` +
               `${num(added)} added, ${num(deleted)} deleted · ` +
               `net ${child.net < 0 ? "−" : "+"}${num(Math.abs(child.net))}, ` +
               `${Math.abs(child.net / length).toFixed(2)}× the ` +
               `${num(child.length)} lines ${name} runs to now · ` +
               `${pct(added, child.churn)} of the work added, against ` +
               // Per changed line, said out loud, because the manual has two
               // averages eight points apart: 76% of every line changed was an
               // addition, where the mean over pages is 68%. The line is the
               // one the row's own figure is on.
               `${pct(ref, 1)} per changed line across the ${corpus().tile}`;
    tr.appendChild(td);
    body.appendChild(tr);
  }

  const thead = document.createElement("thead");
  thead.appendChild(head);
  const grid = document.getElementById("churn-grid");
  grid.hidden = !shown.length;
  grid.replaceChildren(thead, body);
  for (const el of document.querySelectorAll(".churn-balance")) {
    el.textContent = pct(ref, 1);
  }

  const where = state.section
    ? PREFIX + corpus().base + state.section + "/"
    : `the ${corpus().tile}`;
  const silent = children.length - edited.length;
  const notes = [];
  if (!edited.length) {
    // "and none of them was edited" is what the row count already says.
    notes.push(`Nothing in ${where} was edited in this window`);
  } else {
    if (edited.length > shown.length) {
      notes.push(`The ${shown.length} most-edited of ${num(edited.length)} that ` +
                 `changed are shown`);
    }
    if (silent) {
      notes.push(`${num(silent)} of ${num(children.length)} had no edits at all`);
    }
  }
  document.getElementById("churn-note").textContent =
    notes.length ? notes.join("; ") + "." : "";
  const span = `${CHURN.months[0][0]} – ` +
               `${CHURN.months[CHURN.months.length - 1].slice(-1)[0]}`;
  for (const el of document.querySelectorAll(".churn-window")) {
    el.textContent = span;
  }
}

function renderStale() {
  const tbody = document.querySelector("#stale-table tbody");
  tbody.replaceChildren();
  for (const [url, pv, removedTs] of DOCS.unforwarded) {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td");
    td0.className = "path";
    // The URL still resolves — to the "page not found" page, which is the whole
    // finding. Following it is how a reader sees what these arrivals see, and
    // is the one link on this page that is meant to fail.
    td0.appendChild(link(SITE + url, url,
                         "Opens the page not found these readers get"));
    const td1 = document.createElement("td");
    td1.textContent = num(pv);
    const td2 = document.createElement("td");
    // A removal inside the traffic window means the page was live for part of
    // it, so the count is not evidence of a dead link. Say so in the row
    // rather than leaving the reader to compare dates.
    const when = !removedTs
      ? "—"
      : removedTs >= DOCS.windowStart
        ? `${ageLabel(removedTs)} (during)`
        : ageLabel(removedTs);
    // The date leads to the history ending in the removal, which is the record
    // a redirect for this URL would have to be written from.
    if (removedTs) {
      td2.appendChild(quietLink(historyUrl(url), when,
                                "The commits behind this page, ending in the " +
                                "one that removed it"));
    } else {
      td2.textContent = when;
    }
    tr.append(td0, td1, td2);
    tbody.appendChild(tr);
  }

  const kinds = DOCS.staleKinds || {};
  const urls = DOCS.staleKindUrls || {};
  const shown = DOCS.unforwarded.length;
  document.getElementById("stale-caption").textContent =
    `${num(DOCS.unforwardedTotal)} pageviews across ` +
    `${num(DOCS.unforwardedUrls)} URLs` +
    (shown < DOCS.unforwardedUrls ? `, of which the top ${shown} are listed` : "") +
    `. For scale, this is one part of the ${num(DOCS.staleTotal)} pageviews ` +
    `excluded from every other figure on this page because they have no source ` +
    `file at ${DOCS.commit}: ${num(kinds.forwarded || 0)} of those were ` +
    `forwarded by the ${num(DOCS.redirectCount)} redirects on file and reached ` +
    `a page, and ${num(kinds.neverPage || 0)} went to URLs that were never ` +
    `pages here — Sphinx's own search page, downloadable examples, mistyped ` +
    `paths and scanner traffic across ${num(urls.neverPage || 0)} URLs.`;
  renderLoops(kinds, urls);
}

/* Forwarded is not the same as arrived. 404.rst applies redirects.csv in the
   reader's browser, so a destination that is not a page serves 404.rst again,
   which consults the same file again — and where it ends with the source it came
   from, the browser loops, firing a pageview each pass. */
function renderLoops(kinds, urls) {
  const box = document.getElementById("loop-caption");
  const loops = DOCS.looping || [];
  const worst = loops[0];
  box.hidden = !loops.length;
  if (!loops.length) return;
  box.textContent =
    `${num(kinds.looping || 0)} further pageviews are forwarded and never ` +
    `arrive: ${urls.looping} URLs are sent to a destination that matches the ` +
    `same rule again, so the reader's browser loops and each pass records ` +
    `another view. ${num(worst[1])} of them are one URL, ` +
    `${worst[0].slice(PREFIX.length)}, whose destination names a directory the ` +
    `manual does not have. These are one reader counted many times rather than ` +
    `traffic, and a fault in the redirects file rather than in the docs.` +
    (kinds.forwardedNowhere
      ? ` A further ${num(kinds.forwardedNowhere)} are forwarded to a URL that ` +
        `is not a page and is not forwarded on, so the reader is answered with ` +
        `a second "page not found".`
      : "");
}

// ---- controls ---------------------------------------------------------------

function bindControls() {
  window.addEventListener("popstate", () => { readHash(); render(); });
  // Following a link inside the page — a row name in the churn grid — is a
  // fragment navigation, which fires this and not popstate. pushState and
  // replaceState fire neither, so writeHash cannot call this back on itself.
  window.addEventListener("hashchange", () => {
    const before = state.filter;
    readHash();
    render();
    // A row that narrows the table is the one move whose effect is off screen:
    // the grid is a table's height above it, and a reader who saw nothing
    // happen would conclude the name was not a link after all. The same scroll
    // the scatter makes for the same reason.
    if (state.filter && state.filter !== before) {
      document.getElementById("pages-table").scrollIntoView({ behavior: "smooth" });
    }
  });

  const filter = document.getElementById("filter");
  let timer = null;
  filter.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.filter = filter.value; render(); }, 150);
  });

  for (const btn of document.querySelectorAll("#footprint-mode button")) {
    btn.addEventListener("click", () => {
      state.footprint = btn.dataset.footprint;
      render();
    });
  }

  document.querySelectorAll("#pages-table th").forEach((th, i) => {
    th.addEventListener("click", () => {
      if (sortState.col === i) sortState.asc = !sortState.asc;
      else { sortState.col = i; sortState.asc = i === 0; }
      renderTable();
    });
  });
}

function syncControls() {
  const filter = document.getElementById("filter");
  if (filter.value !== state.filter) filter.value = state.filter;
  for (const btn of document.querySelectorAll("#footprint-mode button")) {
    btn.setAttribute("aria-pressed",
                     String(btn.dataset.footprint === state.footprint));
  }
}

// ---- main -------------------------------------------------------------------

function render() {
  syncControls();
  writeHash();
  renderStats();
  renderCrumbs();
  renderSections();
  renderScatter();
  renderConcentration();
  renderChurn();
  renderTable();
}

function init() {
  if (!D || !DOCS) {
    const box = document.getElementById("error");
    box.hidden = false;
    box.textContent = "data.js did not load. Run scripts/aggregate.py to generate it.";
    return;
  }

  const [start, end] = D.period;
  const period = `${start} – ${end}`;
  document.getElementById("period").textContent = period;
  for (const el of document.querySelectorAll(".period-inline")) {
    el.textContent = period;
  }
  document.getElementById("updated").textContent = D.generatedAt.slice(0, 10);
  // The two editions exclusions, in the caveat that explains what they are.
  for (const el of document.querySelectorAll(".en-other")) {
    el.textContent = num(DOCS.enOtherVersions);
  }
  for (const el of document.querySelectorAll(".i18n-other")) {
    el.textContent = num(DOCS.i18nOtherVersions);
  }
  const commit = document.getElementById("commit");
  const link = document.createElement("a");
  link.href = "https://github.com/godotengine/godot-docs/commit/" + DOCS.commit;
  link.textContent = DOCS.commit;
  commit.replaceChildren(link);

  readHash();
  // A link into the class reference carries its own scope. Without this, a
  // shared #s=classes/... URL that lost its c= opens on an empty report.
  if (state.section && !sectionRows().length &&
      DOCS.rows.some((r) => inSection(r, state.section))) {
    state.corpus = "all";
  }

  // Before any chart is constructed. Chart.js reads layout as it sizes its
  // canvases, and the path bar sits above every one of them: filling it first
  // means those reads happen against the height the row will keep.
  renderCrumbs();

  buildSections();
  buildScatter();
  buildFunnel();
  buildEditions();
  bindControls();
  render();
  renderFunnel();
  renderEditions();
  renderStale();
}

init();
