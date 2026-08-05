# Developing

## Tests

```sh
python3 -m unittest discover tests
```

They cover URL canonicalisation, the rst parsers, the two integrity checks — the
redirect walk against a loop entered from either end, the anomaly bars against a
burst from zero — and the invariants the page depends on, including a check that
no identity-derived number reaches the page, and one that per-visitor figures are
still implausible, so if the instance is ever fixed the suite says so instead of
the constraint quietly outliving its reason. Where the CSV export is present they
also cross-check page totals against it, since it was produced independently of
the API path.

## Verifying the page

Two parts of `docs/app.js` are covered: `deno test --allow-read tests/` runs the
churn grid's renderer and every link the page writes out against the data it
ships. The grid's tests exist because shading is set through a style string, and
an alpha of `NaN` is not an error in a browser — the declaration is dropped,
every cell goes white, and the page carries on. That is what happened when a
refactor took a `top` declaration out and the name resolved to `window.top`: no
console output, nothing for `deno check` to see. The link tests are there for
the same kind of silence: a wrong href renders exactly as well as a right one.

**The rest of the page is not covered.** Everything else runs against the Python
payload, and the payload has been right every time the page was wrong. The bug
that made the section chart's pageview bars sum to 89.6% instead of 100% lived
entirely in JavaScript grouping logic.

Headless Chrome renders `file://` directly, which is enough to check both that
the page runs and what it computed:

```sh
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" --headless --disable-gpu --virtual-time-budget=8000 \
  --dump-dom "file://$PWD/docs/index.html#s=tutorials&f=pages" > /tmp/dom.html
```

The dumped DOM carries everything `app.js` wrote into the document — the stat
tiles, both tables, the generated captions. Chart contents live in canvas and
need one extra step: copy `index.html`, append a script that reads
`Chart.getChart("chart-sections").data` into a `<pre>`, dump that instead, and
delete the copy. That is how the 89.6% was found, and it is worth repeating after
any change to the charts.

Two cautions. Chrome's stderr carries `ERROR:gpu/...` lines in headless mode that
have nothing to do with the page. And the path bar's menus are built when opened
and removed when closed, so a dump never contains one — assert on
`menuItems(section)` rather than on the markup.

Not yet automated: a `scripts/check_page.py` wrapping the above would close the
gap, and would sit outside the unit suite since it needs Chrome present.
