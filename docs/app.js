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

// Row layout emitted by scripts/aggregate.py.
const PATH = 0, PV = 1, PREV = 2, WORDS = 3, EDITED = 4, I18N = 5, GENERATED = 6;

const state = { section: "", classes: false, filter: "", scatter: "age",
                footprint: "words" };
const charts = {};

const COLORS = {
  reading: "#0969da",
  pages: "#8c959f",
  line: "#0969da",
  muted: "#8c959f",
  grid: "rgba(208,215,222,.6)",
};

// Blue-to-amber ramp: the two families stay distinguishable in every colour
// vision type, and the steps inside each family differ in lightness, so the
// order survives too.
const AGE_BINS = [
  { max: 183, label: "edited < 6 months ago", color: "#0d366b" },
  { max: 365, label: "6–12 months", color: "#2a78d6" },
  { max: 730, label: "1–2 years", color: "#86b6ef" },
  { max: 1460, label: "2–4 years", color: "#d4a72c" },
  { max: Infinity, label: "4+ years", color: "#7d4e00" },
];

const I18N_BINS = [
  { max: 0.1, label: "little translated reading", color: "#86b6ef" },
  { max: 0.5, label: "up to half again", color: "#2a78d6" },
  { max: 1.5, label: "comparable to English", color: "#0d366b" },
  { max: Infinity, label: "mostly read in translation", color: "#7d4e00" },
];

// ---- state <-> hash ---------------------------------------------------------

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.has("s")) state.section = p.get("s");
  if (p.has("c")) state.classes = p.get("c") === "1";
  if (p.has("q")) state.filter = p.get("q");
  if (p.has("m")) state.scatter = p.get("m") === "i18n" ? "i18n" : "age";
  if (p.has("f")) state.footprint = p.get("f") === "pages" ? "pages" : "words";
}

function writeHash() {
  const p = new URLSearchParams();
  if (state.section) p.set("s", state.section);
  if (state.classes) p.set("c", "1");
  if (state.filter) p.set("q", state.filter);
  if (state.scatter !== "age") p.set("m", state.scatter);
  if (state.footprint !== "words") p.set("f", state.footprint);
  const q = p.toString();
  history.replaceState(null, "", q ? "#" + q : location.pathname);
}

// ---- selection --------------------------------------------------------------

function rel(row) {
  return DOCS.paths[row[PATH]].slice(PREFIX.length);
}

// The class reference is 1,079 generated pages against 512 hand-written ones.
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
   traffic is mostly its own index page should look that way. */
function childrenOf(section) {
  const base = section ? section + "/" : "";
  const out = new Map();
  for (const row of visibleRows()) {
    const r = rel(row);
    if (section && !r.startsWith(base)) continue;
    const rest = r.slice(base.length);
    if (!rest) continue;
    const cut = rest.indexOf("/");
    const key = cut === -1 ? rest : rest.slice(0, cut);
    const entry = out.get(key) || { key, pv: 0, pages: 0, words: 0, dir: cut !== -1 };
    entry.pv += row[PV];
    entry.pages += 1;
    entry.words += row[WORDS];
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

function ageDays(ts) {
  if (!ts) return Infinity;
  return (Date.now() / 1000 - ts) / 86400;
}

function ageLabel(ts) {
  if (!ts) return "unknown";
  const d = new Date(ts * 1000);
  return d.toISOString().slice(0, 7);
}

function binFor(row) {
  if (state.scatter === "i18n") {
    const ratio = row[PV] ? row[I18N] / row[PV] : 0;
    return I18N_BINS.find((b) => ratio <= b.max);
  }
  return AGE_BINS.find((b) => ageDays(row[EDITED]) <= b.max);
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
      { label: "share of reading", backgroundColor: COLORS.reading, data: [] },
      { label: "share of words", backgroundColor: COLORS.pages, data: [] },
    ]},
    options: Object.assign({}, BASE, {
      indexAxis: "y",
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
          return `${num(c.pv)} pageviews\n${num(c.words)} words in ` +
                 `${num(c.pages)} page${c.pages === 1 ? "" : "s"}` +
                 (c.pages ? ` (${num(c.words / c.pages)} each)` : "") +
                 (c.dir ? "\nclick to open" : "");
        } } },
      },
      onClick(evt, els) {
        if (!els.length) return;
        const child = charts.sections.$children[els[0].index];
        if (!child || !child.dir) return;
        state.section = state.section ? state.section + "/" + child.key : child.key;
        render();
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
          label(item) {
            const r = item.raw;
            return [r.label,
                    `${num(r.pv)} pageviews · ${num(r.words)} words`,
                    `translations ${num(r.i18n)} · last edit ${r.age}`];
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

function buildTail() {
  charts.tail = new Chart(document.getElementById("chart-tail"), {
    type: "line",
    data: { labels: [], datasets: [
      { label: "cumulative share of reading", data: [], borderColor: COLORS.line,
        backgroundColor: "rgba(9,105,218,.10)", fill: "origin",
        borderWidth: 2, pointRadius: 0, pointHitRadius: 6 },
    ]},
    options: Object.assign({}, BASE, {
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { title: { display: true, text: "pages, most-read first" },
             grid: { display: false },
             ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 10 } },
        y: { min: 0, max: 100, ticks: { callback: (v) => v + "%" },
             grid: { color: COLORS.grid } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          title: (items) => `${items[0].label} most-read pages`,
          label: (item) => `${item.formattedValue}% of this section's reading`,
        } },
      },
    }),
  });
}

function buildFunnel() {
  charts.funnel = new Chart(document.getElementById("chart-funnel"), {
    type: "bar",
    data: { labels: [], datasets: [
      { label: "pageviews", backgroundColor: COLORS.reading, data: [] },
    ]},
    options: Object.assign({}, BASE, {
      indexAxis: "y",
      scales: {
        x: { beginAtZero: true, grid: { color: COLORS.grid },
             ticks: { callback: (v) => num(v) } },
        y: { grid: { display: false }, ticks: { font: { size: 10 } } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { afterLabel(item) {
          const step = charts.funnel.$steps[item.dataIndex];
          return step.prev
            ? `${step.delta >= 0 ? "+" : ""}${step.delta}% vs the step before`
            : "";
        } } },
      },
    }),
  });
}

function buildDownloads() {
  charts.downloads = new Chart(document.getElementById("chart-downloads"), {
    type: "bar",
    data: { labels: [], datasets: [
      { label: "share of download clicks", backgroundColor: COLORS.reading, data: [] },
      { label: "share of browsing", backgroundColor: COLORS.pages, data: [] },
    ]},
    options: Object.assign({}, BASE, {
      indexAxis: "y",
      scales: {
        x: { beginAtZero: true, ticks: { callback: (v) => v + "%" },
             grid: { color: COLORS.grid } },
        y: { grid: { display: false } },
      },
      plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } } },
    }),
  });

  const versions = D.downloads.byVersion;
  charts.versions = new Chart(document.getElementById("chart-versions"), {
    type: "bar",
    data: {
      labels: versions.map((v) => v[0]),
      datasets: [{ label: "download clicks", backgroundColor: COLORS.reading,
                   data: versions.map((v) => v[1]) }],
    },
    options: Object.assign({}, BASE, {
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, grid: { color: COLORS.grid },
             ticks: { callback: (v) => num(v) } },
      },
      plugins: { legend: { display: false } },
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
    ["read in translation", num(DOCS.i18nViews), "all other languages"],
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

function renderCrumbs() {
  const box = document.getElementById("crumbs");
  box.replaceChildren();
  const parts = state.section ? state.section.split("/") : [];
  const mk = (text, section, last) => {
    if (last) {
      const b = document.createElement("b");
      b.textContent = text;
      return b;
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = text;
    btn.addEventListener("click", () => { state.section = section; render(); });
    return btn;
  };
  box.appendChild(mk("all docs", "", parts.length === 0));
  parts.forEach((part, i) => {
    const sep = document.createElement("span");
    sep.textContent = " / ";
    box.append(sep, mk(part, parts.slice(0, i + 1).join("/"), i === parts.length - 1));
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
    byWords ? "share of words" : "share of pages";
  charts.sections.update();
}

function renderScatter() {
  const rows = sectionRows();
  const bins = state.scatter === "i18n" ? I18N_BINS : AGE_BINS;
  const groups = new Map(bins.map((b) => [b.label, []]));
  for (const row of rows) {
    const bin = binFor(row);
    groups.get(bin.label).push({
      // A page with no reading at all would fall off a log axis; plotting it
      // just below 1 keeps it visible without claiming it was read.
      x: Math.max(row[PV], 0.5),
      y: Math.max(row[WORDS], 1),
      pv: row[PV], words: row[WORDS], i18n: row[I18N],
      age: ageLabel(row[EDITED]),
      label: DOCS.paths[row[PATH]],
    });
  }
  charts.scatter.data.datasets = bins.map((b) => ({
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

function renderTail() {
  const rows = [...sectionRows()].sort((a, b) => b[PV] - a[PV]);
  const total = rows.reduce((a, r) => a + r[PV], 0) || 1;
  let running = 0;
  const labels = [], data = [];
  rows.forEach((r, i) => {
    running += r[PV];
    labels.push(String(i + 1));
    data.push(Math.round((1000 * running) / total) / 10);
  });
  charts.tail.data.labels = labels;
  charts.tail.data.datasets[0].data = data;
  charts.tail.update();

  const marks = [50, 80, 95].map((target) => {
    const idx = data.findIndex((v) => v >= target);
    return idx === -1 ? null : idx + 1;
  });
  const quiet = rows.filter((r) => r[PV] < 100).length;
  const zero = rows.filter((r) => r[PV] === 0).length;
  const where = state.section ? `${PREFIX}${state.section}/` : "the English stable docs";
  document.getElementById("tail-caption").textContent =
    `Of ${num(rows.length)} pages in ${where}, the ${marks[0]} most-read carry half ` +
    `the reading, ${marks[1]} carry 80%, and ${marks[2]} carry 95%. ` +
    `${num(quiet)} pages were opened fewer than 100 times, and ${num(zero)} were not ` +
    `opened at all. A steep curve means reading is concentrated in a few pages — ` +
    `usual for documentation, where most people arrive with one question. It says ` +
    `nothing about whether the flatter tail is worth keeping; the pages there are ` +
    `the ones a search engine sent someone to on the day they needed exactly that.`;
}

function renderFunnel() {
  const byPath = new Map(DOCS.rows.map((r) => [DOCS.paths[r[PATH]], r[PV]]));
  const steps = DOCS.funnel.map((url) => ({
    url,
    short: url.slice(PREFIX.length + "getting_started/".length)
              .replace(/\.html$/, "").replace(/_/g, " "),
    pv: byPath.get(url) || 0,
  }));
  steps.forEach((s, i) => {
    s.prev = i > 0 ? steps[i - 1].pv : null;
    s.delta = s.prev ? Math.round((100 * (s.pv - s.prev)) / s.prev) : 0;
  });
  charts.funnel.$steps = steps;
  charts.funnel.data.labels = steps.map((s) => s.short);
  charts.funnel.data.datasets[0].data = steps.map((s) => s.pv);
  charts.funnel.update();
}

const COLUMNS = [
  { key: "path", label: "page", get: (r) => DOCS.paths[r[PATH]] },
  { key: PV, get: (r) => r[PV], fmt: num },
  { key: "delta", get: (r) => (r[PREV] ? (r[PV] - r[PREV]) / r[PREV] : null) },
  { key: I18N, get: (r) => r[I18N], fmt: num },
  { key: WORDS, get: (r) => r[WORDS], fmt: num },
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
          td.textContent = (p > 0 ? "+" : "") + p + "%";
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

function renderStale() {
  const tbody = document.querySelector("#stale-table tbody");
  tbody.replaceChildren();
  for (const [url, pv] of DOCS.stale) {
    const tr = document.createElement("tr");
    const td0 = document.createElement("td");
    td0.className = "path";
    td0.textContent = url;
    const td1 = document.createElement("td");
    td1.textContent = num(pv);
    tr.append(td0, td1);
    tbody.appendChild(tr);
  }
  document.getElementById("stale-caption").textContent =
    `URLs that still receive traffic but have no source file in godot-docs at ` +
    `${DOCS.commit} — moved pages, old bookmarks, Sphinx's own search page, and ` +
    `scanner noise. In total ${num(DOCS.staleTotal)} pageviews across ` +
    `${num(DOCS.staleUrls)} URLs; the top ${DOCS.stale.length} are listed. These ` +
    `are excluded from every other figure on this page. A path high on this list ` +
    `is one people keep reaching for, which usually means a redirect worth ` +
    `keeping rather than a page worth restoring.`;
}

function renderDownloads() {
  const dl = D.downloads;
  const dlTotal = dl.byFamily.reduce((a, x) => a + x[1], 0) || 1;
  const osTotal = dl.osMix.reduce((a, x) => a + x[1], 0) || 1;
  const osMap = new Map(dl.osMix);
  const families = dl.byFamily.map((x) => x[0]);
  for (const [name] of dl.osMix) {
    if (!families.includes(name)) families.push(name);
  }
  const dlMap = new Map(dl.byFamily);
  charts.downloads.data.labels = families;
  charts.downloads.data.datasets[0].data =
    families.map((f) => Math.round((1000 * (dlMap.get(f) || 0)) / dlTotal) / 10);
  charts.downloads.data.datasets[1].data =
    families.map((f) => Math.round((1000 * (osMap.get(f) || 0)) / osTotal) / 10);
  charts.downloads.update();
}

// ---- controls ---------------------------------------------------------------

function bindControls() {
  const classes = document.getElementById("classes-toggle");
  classes.addEventListener("change", () => {
    state.classes = classes.checked;
    render();
  });

  const filter = document.getElementById("filter");
  let timer = null;
  filter.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.filter = filter.value; render(); }, 150);
  });

  for (const btn of document.querySelectorAll("#scatter-mode button")) {
    btn.addEventListener("click", () => {
      state.scatter = btn.dataset.mode;
      render();
    });
  }

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
  document.getElementById("classes-toggle").checked = state.classes;
  const filter = document.getElementById("filter");
  if (filter.value !== state.filter) filter.value = state.filter;
  for (const btn of document.querySelectorAll("#scatter-mode button")) {
    btn.setAttribute("aria-pressed", String(btn.dataset.mode === state.scatter));
  }
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
  renderTail();
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

  buildSections();
  buildScatter();
  buildTail();
  buildFunnel();
  buildDownloads();
  bindControls();

  readHash();
  render();
  renderFunnel();
  renderStale();
  renderDownloads();
}

init();
