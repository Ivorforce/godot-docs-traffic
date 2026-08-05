/* The churn grid's rendering, run against the data the page actually ships.
 *
 * `deno test --allow-read tests/churn_grid_test.js`
 *
 * The page has no build step and no test runner of its own; this is the one
 * piece of it worth a harness, because the grid says what it has to say in the
 * shading and the shading is set through a style string. An alpha of NaN is not
 * an error in a browser — the declaration is dropped, every cell goes white, and
 * the page carries on. That is exactly what happened when a refactor took the
 * `top` declaration out with the comment above it and the name resolved to
 * `window.top` instead: `deno check` passed, nothing appeared in the console,
 * and the grid quietly stopped saying anything.
 *
 * So the assertions are about the numbers reaching the DOM at all, not about
 * particular values: the figures move every time the data is rebuilt, and a
 * test that pins them would be rewritten rather than read.
 *
 * app.js is a classic script rather than a module, so it is evaluated as a
 * function body with `window` and `document` passed in and its bootstrap call
 * removed. The stub below implements only what renderChurn touches. Free names
 * resolve against Deno's globals rather than a browser's, which is stricter
 * than the page it stands in for and deliberately so: `top`, `name`, `length`
 * and `status` all exist in a browser and will absorb a deleted declaration
 * without a word. Here they throw. If one ever arrives as a number anyway, the
 * alpha assertion is the second net.
 *
 * No dependencies, so this runs offline like the Python tests beside it. */

const assert = (ok, message) => {
  if (!ok) throw new Error(message);
};
const assertEquals = (actual, expected, message) =>
  assert(Object.is(actual, expected) ||
         JSON.stringify(actual) === JSON.stringify(expected),
    `${message ?? "not equal"}: ${JSON.stringify(actual)} != ` +
    JSON.stringify(expected));
const assertMatch = (actual, pattern, message) =>
  assert(pattern.test(actual),
    `${message ?? "no match"}: ${JSON.stringify(actual)} !~ ${pattern}`);

const ROOT = new URL("../docs/", import.meta.url);

class Text {
  constructor(text) { this.textContent = String(text); }
}

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    // Enough of CSSStyleDeclaration for a background string and a custom
    // property; both are read back by the assertions.
    this.style = { setProperty(k, v) { this[k] = v; } };
  }
  set textContent(v) { this.children = v === "" ? [] : [new Text(v)]; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { this.children.push(...cs); }
  replaceChildren(...cs) { this.children = cs; }
  get cells() { return this.children.filter((c) => c.tag === "td"); }
}

/* renderChurn, wired to a stub document, plus the elements it wrote into.
   `section` drills the path bar the way clicking a row would. */
async function render(section = "") {
  const selected = { ".churn-window": [new El("span")],
                     ".churn-balance": [new El("span")] };
  const ids = { "churn-grid": new El("table"), "churn-note": new El("p") };
  const document = {
    createElement: (tag) => new El(tag),
    getElementById: (id) => ids[id] ?? new El("div"),
    querySelectorAll: (sel) => selected[sel] ?? [],
  };

  const win = {};
  new Function("window", await Deno.readTextFile(new URL("data.js", ROOT)))(win);

  const src = await Deno.readTextFile(new URL("app.js", ROOT));
  const body = src.replace(/^init\(\);$/m, "");
  assert(body !== src, "app.js no longer ends in an init() call to strip");

  const app = new Function("window", "document",
    `${body}\nreturn { renderChurn, state };`)(win, document);
  app.state.section = section;
  app.renderChurn();
  return { grid: ids["churn-grid"], note: ids["churn-note"], selected };
}

/* The rows of a rendered grid, as the few things the assertions ask about. */
function readRows(grid) {
  const [, body] = grid.children;
  return body.children.map((tr) => {
    const total = tr.cells.at(-1);
    const bar = total.children[1];
    return {
      name: tr.children[0].textContent,
      total: total.children[0].textContent,
      down: bar.className.includes("down"),
      fill: bar.children[0].style,
      // "… — +21,065 lines on the manual, …", the net the bar is drawn from.
      net: Number(/— ([+−])([\d,]+) lines on the manual/.exec(total.title)
        .slice(1)
        .reduce((sign, n) => (sign === "−" ? "-" : "") + n.replaceAll(",", ""))),
    };
  });
}

/* The alpha out of "rgba(9,105,218,0.317)", or NaN if it is not a number —
 * which is the failure this whole file exists for. */
function alphaOf(background) {
  return Number(/^rgba\(\d+,\d+,\d+,(.+)\)$/.exec(background)?.[1]);
}

Deno.test("every shaded cell carries a usable alpha", async () => {
  const { grid } = await render();
  const [, body] = grid.children;
  const shaded = body.children.flatMap((tr) =>
    tr.cells.filter((td) => td.style.background));

  assert(shaded.length > 0, "no cell was shaded at all");
  for (const td of shaded) {
    const alpha = alphaOf(td.style.background);
    assert(Number.isFinite(alpha),
      `alpha is not a number: ${td.style.background}`);
    // The ramp app.js draws: 0.07 at the faintest, 0.60 where a period changed
    // as many lines as the row is long.
    assert(alpha >= 0.07 && alpha <= 0.6, `alpha ${alpha} is off the ramp`);
  }
  // The scale runs to the busiest cell on screen, so something must reach it.
  assert(shaded.some((td) => alphaOf(td.style.background) === 0.6),
    "nothing reached the top of the scale, so it is anchored somewhere else");
});

Deno.test("an unedited period is left empty rather than shaded", async () => {
  const { grid } = await render();
  const [, body] = grid.children;
  for (const tr of body.children) {
    for (const td of tr.cells) {
      if (td.textContent === "·") {
        assertEquals(td.style.background, undefined,
          "an empty cell was shaded, which reads as a quiet period");
      }
    }
  }
});

Deno.test("the total column carries a figure and a bar", async () => {
  const { grid, selected } = await render();
  const [head] = grid.children;

  assertEquals(head.children[0].children.at(-1).textContent, "changed");
  assertMatch(selected[".churn-balance"][0].textContent, /^\d+%$/);

  for (const row of readRows(grid)) {
    // The figure is a text node beside the bar, not replaced by it.
    assertMatch(row.total, /^[\d.]+k?$/);
    const width = parseFloat(row.fill.width);
    // Each side of the origin is half the bar, so nothing may run past 50%.
    assert(Number.isFinite(width) && width >= 0 && width <= 50,
      `bar fill is ${row.fill.width}`);
  }
});

/* The direction is the whole reason the bar diverges, and it is carried by
   which edge the fill hangs off. Anchored to the wrong one, a section that
   shrank draws as one that grew and reads as perfectly ordinary — no error, no
   odd figure, nothing to notice. So this walks the report until it finds a row
   that actually went the other way rather than trusting the top level, where
   nothing ever does. */
Deno.test("the bar runs the way the row's net went", async () => {
  const top = await render();
  const rows = readRows(top.grid);
  assert(rows.every((r) => !r.down),
    "a top-level section shrank, which would be news");

  const sections = rows.filter((r) => r.name.endsWith("/"))
                       .map((r) => r.name.slice(0, -1));
  let shrinking = null;
  for (const section of sections) {
    const found = readRows((await render(section)).grid).find((r) => r.down);
    if (found) { shrinking = { section, row: found }; break; }
  }
  assert(shrinking, `no row in ${sections.join(", ")} shrank, so the direction ` +
    `this bar exists to draw is untested — check the data before the code`);

  const { section, row } = shrinking;
  assert(row.net < 0, `${section}/${row.name} is drawn shrinking on net ${row.net}`);
  assertEquals(row.fill.right, "50%",
    `${row.name} shrank but its bar is not anchored to the origin`);
  assertEquals(row.fill.left, undefined,
    `${row.name} shrank but its bar runs to the right`);

  for (const s of ["", ...sections]) {
    for (const r of readRows((await render(s)).grid)) {
      assertEquals(r.down, r.net < 0,
        `${s}/${r.name} on net ${r.net} drawn ${r.down ? "shrinking" : "growing"}`);
      assertEquals(r.fill[r.down ? "right" : "left"], "50%",
        `${s}/${r.name} is not anchored to the origin`);
      assertEquals(r.fill[r.down ? "left" : "right"], undefined,
        `${s}/${r.name} is anchored to both edges, so it runs the wrong way`);
    }
  }
});

Deno.test("rows are ordered by what they say they are ordered by", async () => {
  const { grid } = await render();
  const [, body] = grid.children;
  const totals = body.children.map((tr) =>
    Number(/^([\d,]+) lines changed/.exec(tr.cells.at(-1).title)[1]
      .replaceAll(",", "")));
  assertEquals([...totals].sort((a, b) => b - a), totals,
    "the grid is not sorted by lines changed");
});
