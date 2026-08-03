# godot-visitors

Traffic report for [godotengine.org](https://godotengine.org) and the Godot
documentation, built from [Plausible](https://plausible.io) analytics and
published as a static GitHub Pages site.

Its main question: **which documentation pages deserve the most care, and which
are candidates for trimming or merging?** Traffic alone cannot answer that, so
the report joins Plausible's per-page metrics against the `godotengine/godot-docs`
source tree — page length, last edit, and the pages that exist but receive no
traffic at all, which analytics can never report on its own.

Like [godot-area-throughput](https://github.com/Ivorforce/godot-area-throughput),
this is decision support for maintainers, not a ranking. A rarely-read page can
be the one that unblocks a contributor; the report proposes candidates for human
review and nothing more.

## Status

Scaffolding. No pipeline and no site yet.

Before either is written, two things need settling:

- **A discrepancy in the source data.** A 31-day export (2026-07-04 – 2026-08-03)
  reports 4,880,820 pageviews against 77,121 visitors — 63 pageviews per visitor
  per month — while `visits × views_per_visit` from the same export implies about
  1.2M. Everything Plausible derives from the User-Agent (device, browser, OS)
  is self-consistent; everything it derives from the client IP is not. Country
  data covers 6.3% of visitors and the city and region exports are empty. Since
  Plausible builds both the country lookup and the daily visitor hash from the
  client IP, a proxy that does not forward the real client IP would produce
  exactly this: no geography, and many distinct people collapsing onto one
  visitor. That would leave raw pageview counts intact while deflating
  `visitors`, `visits`, and every session-derived metric.

  Until this is confirmed or ruled out against the API, **pageviews** is the
  primary metric here and session-derived numbers are treated as provisional.

- **Publishing clearance.** This draws on Godot's own analytics rather than
  public data. Nothing is published, and no analytics data is committed, before
  the people who run the Plausible instance have agreed to it.

## Setup

No dependencies beyond Python 3 stdlib. Copy `.env.example` to `.env` and add a
Plausible Stats API v2 key with read access to the site; only the fetch step
needs it. Once raw data is committed, aggregation and the site run without a key.

Licensed [MIT](LICENSE).
