"""Unit tests for the aggregation logic.

Two kinds of check here. Most are unit tests over small inline fixtures. The
last class is a cross-check against the dashboard CSV export the project started
from: an independently produced file for the same window, so if the API path and
the export disagree about a page, one of them is wrong.

`data/raw/site-daily.ndjson` also carries visitor counts, and
`TestVisitorMetricsStillUnreliable` checks that the README's reason for ignoring
them still holds — so the pipeline would notice if the instance were fixed.

    python3 -m unittest discover tests
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import aggregate  # noqa: E402
import inventory  # noqa: E402
import plausible  # noqa: E402


class TestCanonical(unittest.TestCase):
    """Read the Docs serves a page at several URLs; traffic must not split."""

    def test_root_forms_collapse(self):
        self.assertEqual(aggregate.canonical("/en/stable/"), "/en/stable/")
        self.assertEqual(aggregate.canonical("/en/stable/index.html"), "/en/stable/")

    def test_directory_gets_index(self):
        self.assertEqual(
            aggregate.canonical("/en/stable/getting_started/first_2d_game/"),
            "/en/stable/getting_started/first_2d_game/index.html")

    def test_already_canonical_is_untouched(self):
        url = "/en/stable/tutorials/2d/index.html"
        self.assertEqual(aggregate.canonical(url), url)

    def test_query_and_fragment_stripped(self):
        self.assertEqual(
            aggregate.canonical("/en/stable/about/faq.html?highlight=x#q1"),
            "/en/stable/about/faq.html")


class TestDownloadParsing(unittest.TestCase):
    def test_parses_platform_and_version(self):
        got = aggregate.parse_download(
            "https://downloads.godotengine.org/?version=4.7.1&flavor=stable"
            "&slug=win64.exe.zip&platform=windows.64")
        self.assertEqual(got["family"], "windows")
        self.assertEqual(got["version"], "4.7.1")
        self.assertFalse(got["mono"])

    def test_detects_dotnet_build(self):
        got = aggregate.parse_download(
            "https://downloads.godotengine.org/?version=4.7.1&flavor=stable"
            "&slug=mono_win64.zip&platform=windows.64")
        self.assertTrue(got["mono"])

    def test_ignores_other_outbound_links(self):
        self.assertIsNone(aggregate.parse_download("https://fund.godotengine.org/"))
        self.assertIsNone(
            aggregate.parse_download("https://downloads.godotengine.org/"))


class TestMonthHelpers(unittest.TestCase):
    def test_month_range_handles_lengths(self):
        self.assertEqual(plausible.month_range("2026-07"), ["2026-07-01", "2026-07-31"])
        self.assertEqual(plausible.month_range("2026-02"), ["2026-02-01", "2026-02-28"])
        self.assertEqual(plausible.month_range("2024-02"), ["2024-02-01", "2024-02-29"])

    def test_month_range_wraps_december(self):
        self.assertEqual(plausible.month_range("2025-12"), ["2025-12-01", "2025-12-31"])

    def test_months_between_crosses_years(self):
        self.assertEqual(plausible.months_between("2025-11", "2026-02"),
                         ["2025-11", "2025-12", "2026-01", "2026-02"])


class TestInventoryParsing(unittest.TestCase):
    def test_url_mapping(self):
        self.assertEqual(inventory.url_for("index.rst"), "/en/stable/")
        self.assertEqual(inventory.url_for("tutorials/2d/index.rst"),
                         "/en/stable/tutorials/2d/index.html")

    def test_word_count_skips_directives_and_underlines(self):
        text = (
            "Page title\n"
            "==========\n"
            "\n"
            "One two three.\n"
            "\n"
            ".. note::\n"
            "\n"
            "    ignored words inside the directive body\n"
            "\n"
            "Four five.\n"
        )
        # "Page title" (2) + "One two three." (3) + "Four five." (2)
        self.assertEqual(inventory.word_count(text), 7)

    def test_word_count_unwraps_roles(self):
        # "See the manual now." — the role markup contributes no words of its own.
        self.assertEqual(inventory.word_count("See :ref:`the manual` now.\n"), 4)

    def test_toctree_entries_resolve_relative(self):
        text = (
            "Intro\n=====\n\n"
            ".. toctree::\n"
            "   :maxdepth: 1\n"
            "\n"
            "   nodes_and_scenes\n"
            "   instancing\n"
        )
        self.assertEqual(
            inventory.toctree_entries(text, "getting_started/step_by_step/index.rst"),
            ["getting_started/step_by_step/nodes_and_scenes.rst",
             "getting_started/step_by_step/instancing.rst"])

    def test_toctree_entries_handle_titles_and_absolute(self):
        text = ".. toctree::\n\n   Named page <about/faq>\n   /classes/index\n"
        self.assertEqual(inventory.toctree_entries(text, "index.rst"),
                         ["about/faq.rst", "classes/index.rst"])


SITE_DAILY = ROOT / "data" / "raw" / "site-daily.ndjson"


@unittest.skipUnless(SITE_DAILY.exists(), "run scripts/fetch.py first")
class TestVisitorMetricsStillUnreliable(unittest.TestCase):
    """The README justifies using pageviews only by showing that per-visitor
    figures collapsed on 2025-10-01 and stayed collapsed. If that ever stops
    being true — the instance is fixed, or the diagnosis was wrong — this fails,
    and the report should reconsider what it can use."""

    @classmethod
    def setUpClass(cls):
        per_day = {}
        for line in SITE_DAILY.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            agg = per_day.setdefault(row["d"], [0, 0])
            agg[0] += row["pv"]
            agg[1] += row["v"]
        cls.days = sorted(per_day)
        cls.per_day = per_day

    def ratio(self, day):
        pv, visitors = self.per_day[day]
        return pv / max(visitors, 1)

    def test_ratio_was_sane_before(self):
        cutoff = plausible.VISITOR_METRICS_UNRELIABLE_FROM
        # Stop a day short: the change landed part-way through the day before.
        before = [d for d in self.days if d < cutoff][-10:-1]
        self.assertTrue(before, "no history before the cutoff")
        for day in before:
            self.assertLess(self.ratio(day), 8, f"{day} should look normal")

    def test_ratio_is_still_implausible_now(self):
        recent = self.days[-30:]
        worst = min(self.ratio(d) for d in recent)
        self.assertGreater(
            worst, 20,
            "per-visitor figures now look plausible again — if the instance was "
            "fixed, revisit the README and consider using visitor metrics")


EXPORT = Path("/Users/lukas/dev/research/"
              "plausible-export-godotengine.org-2026-07-04_2026-08-03")
DATA_JS = ROOT / "docs" / "data.js"


def load_payload():
    text = DATA_JS.read_text()
    return json.loads(text[text.index("{"):].rstrip().rstrip(";"))


@unittest.skipUnless(DATA_JS.exists(), "run scripts/aggregate.py first")
class TestGeneratedPayload(unittest.TestCase):
    """Invariants the page relies on. These guard the data contract, not the
    numbers, so they stay valid as traffic changes."""

    @classmethod
    def setUpClass(cls):
        cls.payload = load_payload()
        cls.docs = cls.payload["docs"]

    def test_rows_align_with_paths(self):
        paths = self.docs["paths"]
        self.assertEqual(len(paths), len(self.docs["rows"]))
        for i, row in enumerate(self.docs["rows"]):
            self.assertEqual(row[0], i, "row path index must match its position")

    def test_every_path_is_under_the_prefix(self):
        for path in self.docs["paths"]:
            self.assertTrue(path.startswith(self.docs["prefix"]), path)

    def test_funnel_paths_exist(self):
        known = set(self.docs["paths"])
        for url in self.docs["funnel"]:
            self.assertIn(url, known)

    def test_funnel_follows_the_toctree_order(self):
        # The introduction must come before the 2D game, whatever the traffic.
        order = [u for u in self.docs["funnel"]]
        intro = order.index("/en/stable/getting_started/introduction/index.html")
        game = order.index("/en/stable/getting_started/first_2d_game/index.html")
        self.assertLess(intro, game)

    def test_no_identity_metric_reaches_the_page(self):
        """Per-visitor and per-session figures are unreliable on this instance
        (see README). The page says everything on it is pageviews; no field
        derived from identity may reach it, in any section.

        Checked against field names, not the serialised blob: page paths are
        data here, and there is a class_websocketpeer.html in the class
        reference that has nothing to do with sessions.
        """
        def keys(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield k
                    yield from keys(v)
            elif isinstance(node, list):
                for item in node:
                    yield from keys(item)

        for key in keys(self.payload):
            low = key.lower()
            for term in ("visitor", "bounce", "duration", "session"):
                self.assertNotIn(term, low, f"field {key!r} carries {term} data")

    def test_row_schema_is_the_documented_seven_columns(self):
        """app.js indexes rows positionally; a widened row silently shifts every
        column it reads."""
        for row in self.docs["rows"]:
            self.assertEqual(len(row), 7)

    def test_sections_series_match_month_count(self):
        for name, series in self.docs["sections"].items():
            self.assertEqual(len(series), len(self.payload["months"]), name)


@unittest.skipUnless(EXPORT.exists() and DATA_JS.exists(),
                     "needs the CSV export and a generated data.js")
class TestAgainstCsvExport(unittest.TestCase):
    """Cross-check against the dashboard CSV export.

    The export covers 2026-07-04..2026-08-03 and this report covers July, so the
    windows overlap but do not match — the comparison is deliberately loose. It
    exists to catch a wrong hostname filter or a broken path join, either of
    which would be off by far more than the window difference.
    """

    @classmethod
    def setUpClass(cls):
        import csv
        cls.payload = load_payload()
        with (EXPORT / "pages.csv").open() as f:
            cls.export = {r["name"]: int(r["pageviews"]) for r in csv.DictReader(f)}
        docs = cls.payload["docs"]
        cls.ours = {p: r[1] for p, r in zip(docs["paths"], docs["rows"])}

    def test_top_pages_are_in_the_same_ballpark(self):
        overlap = [(p, self.export[p], self.ours[p])
                   for p in self.export if p in self.ours and self.export[p] > 20000]
        self.assertGreaterEqual(len(overlap), 5,
                                "expected several high-traffic pages in both")
        for path, exported, ours in overlap:
            ratio = ours / exported
            self.assertTrue(0.5 < ratio < 1.6,
                            f"{path}: export {exported}, ours {ours} (x{ratio:.2f})")

    def test_our_total_exceeds_the_truncated_export(self):
        """The export stops at 100 rows; a full fetch must find more, not less."""
        export_docs = sum(v for k, v in self.export.items()
                          if k.startswith("/en/stable/"))
        ours = sum(self.ours.values())
        self.assertGreater(ours, export_docs)


if __name__ == "__main__":
    unittest.main()
