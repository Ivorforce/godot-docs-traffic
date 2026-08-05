/* Godot documentation traffic — page logic.

   All data arrives in one generated data.js (window.DATA), so the page opens
   straight from disk with no server. State (section, class-reference toggle,
   filter, scatter colouring) mirrors into location.hash.

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
const PATH = 0, PV = 1, PREV = 2, WORDS = 3, EDITED = 4, I18N = 5, GENERATED = 6,
      LINES = 7, NET = 8, LENGTH = 9;

// The per-page monthly series behind the churn grid, and the months it covers.
const CHURN = DOCS.churn;

const state = { section: "", classes: false, filter: "", footprint: "words" };
const charts = {};

const COLORS = {
  reading: "#0969da",
  pages: "#8c959f",
  line: "#0969da",
  muted: "#8c959f",
  grid: "rgba(208,215,222,.6)",
};

/* A blue-to-amber ramp: the two families stay distinguishable in every colour
   vision type, and the steps inside each family differ in lightness, so the
   order survives too. Amber is the end to act on, as everywhere else here.

   Turnover is lines changed over the churn window against the page's length
   now, the same measure the churn grid shades by, so the two agree about what
   a busy page is. It is the one figure tested here that says something the
   position does not: against pageviews it measures −0.09 and against words
   0.01, where colouring by month-on-month change or by translation share both
   leaned on the horizontal axis.

   The first threshold is the lower quartile rather than zero exactly. Whether
   a quiet page received one typo fix in two years is an accident of who walked
   past it, and splitting there put only 3 pages in the amber bin inside the
   long-and-seldom-read corner, where the quartile puts 17 — a list someone can
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
const FUNNEL_STYLES = [
  { color: "#0d366b", point: "circle" },
  { color: "#2a78d6", point: "triangle" },
  { color: "#d4a72c", point: "rect" },
  { color: "#7d4e00", point: "rectRot" },
];

// ---- state <-> hash ---------------------------------------------------------

// Every key is assigned, absent or not: this also runs on popstate, where a key
// missing from the entry being restored means "back to the default" rather than
// "keep whatever is on screen".
function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  state.section = p.get("s") || "";
  state.classes = p.get("c") === "1";
  state.filter = p.get("q") || "";
  state.footprint = p.get("f") === "pages" ? "pages" : "words";
}

function writeHash() {
  const p = new URLSearchParams();
  if (state.section) p.set("s", state.section);
  if (state.classes) p.set("c", "1");
  if (state.filter) p.set("q", state.filter);
  if (state.footprint !== "words") p.set("f", state.footprint);
  const q = p.toString();
  // Changing scope is a navigation and Back should undo it; typing in the filter
  // or flipping a chart mode only rewrites the current entry. Comparing against
  // the URL rather than against previous state also makes this correct on
  // popstate, where the URL already holds the state being restored and so no
  // entry is added.
  const cur = new URLSearchParams(location.hash.slice(1));
  const moved = (cur.get("s") || "") !== state.section ||
                (cur.get("c") === "1") !== state.classes;
  const url = q ? "#" + q : location.pathname;
  if (moved) history.pushState(null, "", url);
  else history.replaceState(null, "", url);
}

// ---- selection --------------------------------------------------------------

function rel(row) {
  return DOCS.paths[row[PATH]].slice(PREFIX.length);
}

// The class reference is 1,079 generated pages against 511 hand-written ones.
// Left in, it decides the shape of every page-count comparison on the page, so
// it is opt-in everywhere rather than only in the chart that motivated it.
function visibleRows() {
  return DOCS.rows.filter((r) => state.classes || !r[GENERATED]);
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
    // second most-read page there is, so dropping it silently cost the reading
    // bars a tenth of their total while leaving the word bars intact.
    const cut = rest ? rest.indexOf("/") : -1;
    const key = !rest ? "(landing page)" : cut === -1 ? rest : rest.slice(0, cut);
    const entry = out.get(key) ||
      { key, pv: 0, pages: 0, words: 0, length: 0, churn: 0, net: 0,
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

// A fraction as a signed percentage. The sign is the point — "+8%" and "8%"
// read differently when the neighbouring figure may be negative.
function signed(v) {
  const p = Math.round(100 * v);
  return (p > 0 ? "+" : "") + p + "%";
}

/* 24,229 -> "24k". Only the churn grid uses this: eleven columns of thousands
   separators do not fit the page, and the grid is read for shape and order of
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

function i18nRatio(row) {
  return row[PV] ? row[I18N] / row[PV] : 0;
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
    ctx.fillText("long · seldom read", area.left + pad, area.top + pad);
    ctx.textAlign = "right";
    ctx.fillText("long · widely read", area.right - pad, area.top + pad);
    ctx.textBaseline = "bottom";
    ctx.fillText("short · widely read", area.right - pad, area.bottom - pad);
    ctx.textAlign = "left";
    ctx.fillText("short · seldom read", area.left + pad, area.bottom - pad);
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
      { label: "reading — % of all pageviews", backgroundColor: COLORS.reading,
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
          // of a legend key alone. Translation reading rides along because it
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
                    `translations ${num(r.i18n)} · ${r.ratio.toFixed(2)}× the ` +
                      `English figure`,
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
  const stats = [
    ["pageviews", num(total), "English stable docs"],
    ["pages", num(rows.length), state.classes ? "including class reference" : "hand-written"],
    ["carry half the reading", num(half), `${pct(half, rows.length)} of pages`],
    ["under 100 views", num(quiet), `${pct(quiet, rows.length)} of pages`],
    ["read in translation", num(DOCS.i18nViews),
     `all other languages · ${num(DOCS.i18nOtherVersions)} more on pinned releases`],
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

const ROOT_LABELS = { true: "Full Manual + Class Reference", false: "Full Manual" };

let openMenu = null;

function closeMenu() {
  if (!openMenu) return;
  document.removeEventListener("click", openMenu.onOutside);
  openMenu.menu.remove();
  openMenu.button.setAttribute("aria-expanded", "false");
  openMenu = null;
}

/* The two corpus entries sit at the top of the root menu rather than in a
   checkbox of their own: the class reference is 1,079 generated pages under
   classes/ and nothing else lives there, so "include it" and "look at this part
   of the manual" are the same kind of choice and belong in the same list. */
function menuItems(section) {
  const items = [];
  if (!section) {
    items.push({ label: ROOT_LABELS.true, checked: state.classes,
                 run: () => { state.classes = true; state.section = ""; } });
    items.push({ label: ROOT_LABELS.false, checked: !state.classes,
                 run: () => { state.classes = false; state.section = ""; } });
    items.push({ separator: true });
  }
  const children = childrenOf(section);
  const total = children.reduce((a, c) => a + c.pv, 0);
  for (const child of children) {
    if (!child.dir) continue;
    const target = section ? section + "/" + child.key : child.key;
    items.push({ label: child.key, note: pct(child.pv, total),
                 run: () => { state.section = target; } });
  }
  return items;
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
  const heading = section ? `share of ${label} reading` : "share of all reading";
  const { menu, entries } = buildMenu(button, heading, menuItems(section));
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

  add(ROOT_LABELS[state.classes], "", parts.length === 0);
  parts.forEach((part, i) => {
    const sep = document.createElement("span");
    sep.className = "crumb-sep";
    sep.textContent = "/";
    box.appendChild(sep);
    add(part, parts.slice(0, i + 1).join("/"), i === parts.length - 1);
  });
}

function renderSections() {
  const children = childrenOf(state.section);
  const rows = sectionRows();
  const totalPv = rows.reduce((a, r) => a + r[PV], 0) || 1;
  const byWords = state.footprint === "words";
  const size = (c) => (byWords ? c.words : c.pages);
  const totalSize = rows.reduce((a, r) => a + (byWords ? r[WORDS] : 1), 0) || 1;

  charts.sections.$children = children;
  charts.sections.data.labels = children.map((c) => c.key + (c.dir ? "/" : ""));
  charts.sections.data.datasets[0].data =
    children.map((c) => Math.round((1000 * c.pv) / totalPv) / 10);
  charts.sections.data.datasets[1].data =
    children.map((c) => Math.round((1000 * size(c)) / totalSize) / 10);
  charts.sections.data.datasets[1].label =
    byWords ? "size — % of all words" : "size — % of all pages";
  charts.sections.update();
}

function renderScatter() {
  const rows = sectionRows();
  const groups = new Map(CHURN_BINS.map((b) => [b.label, []]));
  for (const row of rows) {
    groups.get(binFor(row).label).push({
      // A page with no reading at all would fall off a log axis; plotting it
      // just below 1 keeps it visible without claiming it was read.
      x: Math.max(row[PV], 0.5),
      y: Math.max(row[WORDS], 1),
      pv: row[PV], words: row[WORDS], i18n: row[I18N],
      ratio: i18nRatio(row),
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
    data: groups.get(b.label),
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

// How lopsided the reading is, in words rather than as a chart: at this
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
  const where = state.section ? `${PREFIX}${state.section}/` : "the English stable docs";
  let text =
    `Of ${num(pv.length)} pages in ${where}, the ${num(rank(50))} most-read carry ` +
    `half the reading, ${num(rank(80))} carry 80%, and ${num(rank(95))} carry 95% ` +
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
      ? "Every tutorial's landing page is read more than any page inside it. "
      : "") +
    `Landing page, then the share of it that reaches the first step: ` +
    parts.join("; ") + ".";
}

const COLUMNS = [
  { key: "path", label: "page", get: (r) => DOCS.paths[r[PATH]] },
  { key: PV, get: (r) => r[PV], fmt: num },
  { key: "delta", get: (r) => (r[PREV] ? (r[PV] - r[PREV]) / r[PREV] : null) },
  { key: I18N, get: (r) => r[I18N], fmt: num },
  { key: WORDS, get: (r) => r[WORDS], fmt: num },
  { key: LINES, get: (r) => r[LINES], fmt: num },
  // Signed, because the sign is the whole reading: +900 against 900 changed
  // lines is a page that was written, 0 against 900 is one that was replaced.
  { key: NET, get: (r) => r[NET], fmt: (v) => (v > 0 ? "+" : "") + num(v) },
  { key: EDITED, get: (r) => r[EDITED], fmt: (v) => (v ? ageLabel(v) : "—") },
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
    const a = document.createElement("a");
    a.href = "https://docs.godotengine.org" + path;
    a.textContent = path.slice(PREFIX.length) || "(index)";
    a.title = "Open the page";
    td0.appendChild(a);
    tr.appendChild(td0);

    for (const c of COLUMNS.slice(1)) {
      const td = document.createElement("td");
      const v = c.get(row);
      if (c.key === "delta") {
        if (v === null) {
          td.textContent = "—";
        } else {
          const p = Math.round(v * 100);
          td.textContent = signed(v);
          td.style.color = p > 15 ? "#1a7f37" : p < -15 ? "#cf222e" : "#57606a";
        }
      } else {
        td.textContent = c.fmt ? c.fmt(v) : v;
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
  const cell = (tag, text) => {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  };

  const head = document.createElement("tr");
  head.appendChild(cell("th", "page or section"));
  for (const period of CHURN.periods) {
    // The newest column stops at the docs commit rather than at the end of its
    // quarter, so it is short by however much of the period is still to come.
    // Marked in the header, because read as a full column it looks like work
    // falling off.
    const th = cell("th", period.label + (period.partial ? "*" : ""));
    if (period.partial) th.title = "part-period: ends at the docs commit";
    head.appendChild(th);
  }
  head.append(cell("th", "added"), cell("th", "deleted"));

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
    const label = cell("th", name);
    label.scope = "row";
    // The column is narrow enough to clip a long page name, so the full one is
    // always available without leaving the row.
    label.title = name;
    tr.appendChild(label);
    child.periods.forEach((lines, i) => {
      const td = cell("td", lines ? compact(lines) : "·");
      td.className = lines ? "cell" : "cell empty";
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
    // Added and deleted are the row's total and net read apart again.
    for (const value of [(child.churn + child.net) / 2,
                         (child.churn - child.net) / 2]) {
      const td = cell("td", compact(value));
      td.title = `${num(value)} lines`;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }

  const thead = document.createElement("thead");
  thead.appendChild(head);
  const grid = document.getElementById("churn-grid");
  grid.hidden = !shown.length;
  grid.replaceChildren(thead, body);

  const where = state.section ? PREFIX + state.section + "/" : "the manual";
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
    td0.textContent = url;
    const td1 = document.createElement("td");
    td1.textContent = num(pv);
    const td2 = document.createElement("td");
    // A removal inside the traffic window means the page was live for part of
    // it, so the count is not evidence of a dead link. Say so in the row
    // rather than leaving the reader to compare dates.
    td2.textContent = !removedTs
      ? "—"
      : removedTs >= DOCS.windowStart
        ? `${ageLabel(removedTs)} (during)`
        : ageLabel(removedTs);
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
    `file at ${DOCS.commit}: ${num(kinds.redirected || 0)} of those were ` +
    `forwarded by the ${num(DOCS.redirectCount)} redirects on file, and ` +
    `${num(kinds.neverPage || 0)} went to URLs that were never pages here — ` +
    `Sphinx's own search page, downloadable examples, mistyped paths and ` +
    `scanner traffic across ${num(urls.neverPage || 0)} URLs.`;
}

// ---- controls ---------------------------------------------------------------

function bindControls() {
  window.addEventListener("popstate", () => { readHash(); render(); });

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
  const commit = document.getElementById("commit");
  const link = document.createElement("a");
  link.href = "https://github.com/godotengine/godot-docs/commit/" + DOCS.commit;
  link.textContent = DOCS.commit;
  commit.replaceChildren(link);

  readHash();
  // A link into the class reference carries its own scope. Without this, a
  // shared #s=classes/... URL that lost its c=1 opens on an empty report.
  if (state.section && !sectionRows().length &&
      DOCS.rows.some((r) => inSection(r, state.section))) {
    state.classes = true;
  }

  // Before any chart is constructed. Chart.js reads layout as it sizes its
  // canvases, and the path bar sits above every one of them: filling it first
  // means those reads happen against the height the row will keep.
  renderCrumbs();

  buildSections();
  buildScatter();
  buildFunnel();
  bindControls();
  render();
  renderFunnel();
  renderStale();
}

init();
