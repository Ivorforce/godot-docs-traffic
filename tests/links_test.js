/* The links the page writes out, against the data it ships.
 *
 * `deno test --allow-read tests/links_test.js`
 *
 * Every source and history link on the page is built by taking a URL apart and
 * putting a source path back together, which inverts scripts/inventory.py's
 * url_for in a second language. Nothing in a browser notices when an inversion
 * drifts: the anchors still render, still look right, and land on a GitHub 404
 * that only whoever follows one ever sees. So this walks all 1,590 pages of
 * data/inventory.json — the file that holds the answer, since inventory.py
 * records the source path it read each page from — and asks app.js for the same
 * one.
 *
 * The churn grid's row names are checked here rather than in churn_grid_test.js
 * because what is being tested is where they lead, not how the grid draws.
 *
 * app.js is a classic script, so it is evaluated as a function body with
 * `window` and `document` passed in, the same as the grid's tests do. */

const assert = (ok, message) => {
  if (!ok) throw new Error(message);
};
const assertEquals = (actual, expected, message) =>
  assert(Object.is(actual, expected),
    `${message ?? "not equal"}: ${JSON.stringify(actual)} != ` +
    JSON.stringify(expected));

const ROOT = new URL("../", import.meta.url);

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.style = { setProperty(k, v) { this[k] = v; } };
  }
  set textContent(v) { this.children = v === "" ? [] : [{ textContent: String(v) }]; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  appendChild(c) { this.children.push(c); return c; }
  append(...cs) { this.children.push(...cs); }
  replaceChildren(...cs) { this.children = cs; }
}

/* app.js's own functions, plus the grid rendered into a stub document. */
async function load() {
  const ids = { "churn-grid": new El("table"), "churn-note": new El("p") };
  const document = {
    createElement: (tag) => new El(tag),
    getElementById: (id) => ids[id] ?? new El("div"),
    querySelectorAll: () => [],
  };

  const win = {};
  new Function("window", await Deno.readTextFile(new URL("docs/data.js", ROOT)))(win);

  const src = await Deno.readTextFile(new URL("docs/app.js", ROOT));
  const body = src.replace(/^init\(\);$/m, "");
  assert(body !== src, "app.js no longer ends in an init() call to strip");

  const app = new Function("window", "document", `${body}
    return { srcPath, sourceUrl, historyUrl, renderChurn, state, DOCS };`)(
    win, document);
  return { app, grid: ids["churn-grid"] };
}

const inventory = JSON.parse(
  await Deno.readTextFile(new URL("data/inventory.json", ROOT)));

Deno.test("every page's source path is the one the inventory read it from",
  async () => {
    const { app } = await load();
    const pages = Object.entries(inventory.pages);
    assert(pages.length > 1000, `only ${pages.length} pages in the inventory`);
    for (const [url, page] of pages) {
      assertEquals(app.srcPath(url), page.src, `${url} resolves to the wrong file`);
    }
  });

/* Removed pages are the other half of the same mapping, and the half with no
   entry in `pages` to check against: their links are built from a URL whose
   file is gone. url_for is invertible either way round, so this asks the
   question from the other side — the source paths the inventory recorded as
   removed, put through url_for's shape here and back through app.js. */
Deno.test("a removed page's URL still resolves to the file that was removed",
  async () => {
    const { app } = await load();
    const removed = Object.keys(inventory.removed);
    assert(removed.length > 0, "no removed pages in the inventory to check");
    const prefix = inventory.urlPrefix;
    for (const url of removed) {
      const src = app.srcPath(url);
      assert(src.endsWith(".rst"), `${url} resolves to ${src}`);
      assertEquals(
        src === "index.rst" ? prefix : prefix + src.slice(0, -4) + ".html", url,
        `${url} does not survive the round trip`);
    }
  });

Deno.test("source links are pinned to the commit the figures were read at",
  async () => {
    const { app } = await load();
    const commit = app.DOCS.commit;
    assert(/^[0-9a-f]{7,40}$/.test(commit), `${commit} is not a commit`);
    for (const url of ["/en/stable/", "/en/stable/tutorials/2d/2d_lights_and_shadows.html"]) {
      assertEquals(app.sourceUrl(url),
        `https://github.com/godotengine/godot-docs/blob/${commit}/${app.srcPath(url)}`);
      assertEquals(app.historyUrl(url),
        `https://github.com/godotengine/godot-docs/commits/${commit}/${app.srcPath(url)}`);
    }
  });

/* Where the grid's row names lead. Every one of them stays in the report — a
   section opens it on itself, a page narrows the table to that page — and the
   two are told apart by the trailing slash the name is drawn with. A row that
   led out to the docs or to GitHub would be a column whose destination has to
   be guessed from the name, which is what this column stopped doing. */
Deno.test("a grid row leads where its name says it does", async () => {
  const { app, grid } = await load();
  for (const section of ["", "tutorials", "getting_started"]) {
    app.state.section = section;
    app.renderChurn();
    const [, body] = grid.children;
    assert(body.children.length > 0, `no rows at ${section || "the root"}`);
    for (const tr of body.children) {
      const [anchor] = tr.children[0].children;
      const name = anchor.textContent;
      // Read the way the page reads its own hash, not compared as a string:
      // URLSearchParams spells a path separator "%2F" going out and takes it
      // back as "/", and what matters is the view that arrives.
      assert(anchor.href && anchor.href.startsWith("#"),
        `${name} leads to ${anchor.href}, which is outside the report`);
      const params = new URLSearchParams(anchor.href.slice(1));
      if (name.endsWith("/")) {
        assertEquals(params.get("s"),
          (section ? section + "/" : "") + name.slice(0, -1),
          `${name} does not open the report on itself`);
        assertEquals(params.get("q"), null,
          `${name} is a section but also narrows the table`);
      } else {
        const path = params.get("q");
        assert(path && path.startsWith("/en/stable/"),
          `${name} narrows the table to ${path}`);
        assert(path.endsWith(name) ||
               // The docs root has no file name of its own.
               (name === "(landing page)" && path === "/en/stable/"),
          `${name} narrows the table to ${path}`);
        // The filter has to name a page the table actually holds, or the row
        // leads to an empty table — which is what a path built by hand from a
        // row name, rather than taken from the page it came from, would do.
        assert(inventory.pages[path], `${path} is not a page of the manual`);
      }
    }
  }
});
