/* The scatter's points, run against the data the page actually ships.
 *
 * `deno test --allow-read tests/scatter_test.js`
 *
 * One thing is tested here and it is an ordering. The chart is configured with
 * `parsing: false`, which is Chart.js's promise that the points arrive in its
 * internal shape — and, undocumented at the call site, its licence to mark
 * every dataset sorted without looking. It then takes the horizontal range from
 * the first and last point of each dataset alone. Handed the pages in the order
 * the payload lists them, that range is two arbitrary pages wide and every page
 * outside it is clipped away at the edge of the plot: 47 of the manual's 511
 * were off the left of the chart, and the class reference's most-read page off
 * the right. No error, no gap, no console message — a full-looking cloud of
 * points with the quiet ones cut off, which is precisely the corner the chart
 * is for.
 *
 * So the assertion is the invariant Chart.js is relying on, written out: the
 * range derived from first and last is the range over all of them. Sorting each
 * dataset is what makes it true, and this is what says so.
 *
 * The harness is the one in churn_grid_test.js — app.js evaluated as a function
 * body with `window` and `document` passed in — with a stub chart standing in
 * for the canvas, since nothing here needs anything drawn. */

const assert = (ok, message) => {
  if (!ok) throw new Error(message);
};
const assertEquals = (actual, expected, message) =>
  assert(Object.is(actual, expected) ||
         JSON.stringify(actual) === JSON.stringify(expected),
    `${message ?? "not equal"}: ${JSON.stringify(actual)} != ` +
    JSON.stringify(expected));

const ROOT = new URL("../docs/", import.meta.url);

/* renderScatter, wired to a chart that records rather than draws. `corpus` and
   `section` set the view the way the corpus buttons and the path bar would. */
async function render(corpus = "manual", section = "") {
  const document = {
    createElement: () => ({ style: {}, appendChild() {}, append() {} }),
    getElementById: () => null,
    querySelectorAll: () => [],
  };

  const win = {};
  new Function("window", await Deno.readTextFile(new URL("data.js", ROOT)))(win);

  const src = await Deno.readTextFile(new URL("app.js", ROOT));
  const body = src.replace(/^init\(\);$/m, "");
  assert(body !== src, "app.js no longer ends in an init() call to strip");

  const app = new Function("window", "document",
    `${body}\nreturn { renderScatter, charts, state, sectionRows };`)(win, document);
  app.charts.scatter = {
    data: { datasets: [] },
    options: { plugins: {}, scales: { x: {} } },
    update() {},
  };
  app.state.corpus = corpus;
  app.state.section = section;
  app.renderScatter();
  return { datasets: app.charts.scatter.data.datasets,
           axisMin: app.charts.scatter.options.scales.x.min,
           rows: app.sectionRows().length };
}

/* Every view the corpus buttons offer, and one drill-down, because the bug this
   guards was a property of the order pages happened to arrive in and that order
   changes with the filter. */
const VIEWS = [["manual", ""], ["all", ""], ["classes", ""],
               ["manual", "tutorials"]];

Deno.test("no page is plotted outside the axes it set", async () => {
  for (const [corpus, section] of VIEWS) {
    const { datasets, rows } = await render(corpus, section);
    const points = datasets.flatMap((d) => d.data);
    assertEquals(points.length, rows,
      `${corpus}/${section}: pages went missing between the rows and the chart`);
    assert(points.length > 0, `${corpus}/${section} plotted nothing`);

    // What Chart.js will do with `_sorted` set, against what the data holds.
    const ends = datasets.filter((d) => d.data.length)
                         .flatMap((d) => [d.data[0].x, d.data.at(-1).x]);
    const xs = points.map((p) => p.x);
    assertEquals([Math.min(...ends), Math.max(...ends)],
      [Math.min(...xs), Math.max(...xs)],
      `${corpus}/${section}: the range Chart.js reads off the first and last ` +
      `point is not the range of the points, so the rest are clipped away`);
  }
});

/* The floor is a display convention, and a convention that goes unmarked is a
   misread left edge: a reader takes the leftmost points for the least-viewed
   pages there are. Three things have to hold together for it to stay honest —
   nothing is drawn left of the floor, everything drawn there is marked, and the
   axis leaves the stack room to sit inside the plot instead of half outside
   it. The last one is conditional, so it is checked in both directions. */
Deno.test("pages below the floor are drawn at it, marked, and left of nothing", async () => {
  let clampedSomewhere = 0;
  for (const [corpus, section] of VIEWS) {
    const { datasets, axisMin } = await render(corpus, section);
    const points = datasets.flatMap((d) => d.data);
    const floor = Math.min(...points.map((p) => p.x));
    assert(floor >= 10, `${corpus}/${section}: a point sits at ${floor}, below the floor`);

    for (const p of points) {
      assertEquals(p.clamped, p.x === 10 && p.pv < 10,
        `${corpus}/${section}: ${p.label} at ${p.pv} pageviews is marked wrong`);
    }

    const clamped = points.filter((p) => p.clamped);
    clampedSomewhere += clamped.length;
    if (clamped.length) {
      assert(axisMin !== undefined && axisMin < 10,
        `${corpus}/${section}: ${clamped.length} pages stand on the floor and ` +
        `the axis starts at ${axisMin}, so the stack draws half outside the plot`);
    } else {
      assertEquals(axisMin, undefined,
        `${corpus}/${section}: nothing is on the floor, so the axis should ` +
        `not be held open below it`);
    }
  }
  assert(clampedSomewhere > 0,
    "no view had a page below the floor, so none of this was exercised");
});

Deno.test("each series is ascending in pageviews, which is what earns that", async () => {
  for (const [corpus, section] of VIEWS) {
    const { datasets } = await render(corpus, section);
    for (const d of datasets) {
      const xs = d.data.map((p) => p.x);
      assertEquals([...xs].sort((a, b) => a - b), xs,
        `${corpus}/${section}: "${d.label}" is out of order`);
    }
  }
});
