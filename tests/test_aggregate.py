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

import datetime
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import aggregate  # noqa: E402
import fetch  # noqa: E402
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


class TestI18nKeys(unittest.TestCase):
    """Translation paths must land on the key the English side produces.

    `build_docs` keys the manual's landing page as "index.html". The rollup
    stores it as the empty remainder of `/ja/4.x/`, which is neither a directory
    nor a file name, and is the single largest key in the file.
    """

    def fold(self, rows, field="c"):
        original = aggregate.store.read
        aggregate.store.read = lambda kind, month: rows
        try:
            return aggregate.load_i18n("2026-07", field)
        finally:
            aggregate.store.read = original

    def test_landing_page_folds_onto_index(self):
        out = self.fold([{"p": "", "pv": 40, "c": 30},
                         {"p": "index.html", "pv": 5, "c": 5}])
        self.assertEqual(out, {"index.html": 35})

    def test_directory_folds_onto_index(self):
        out = self.fold([{"p": "tutorials/2d/", "pv": 7, "c": 7},
                         {"p": "tutorials/2d/index.html", "pv": 3, "c": 3}])
        self.assertEqual(out, {"tutorials/2d/index.html": 10})

    def test_ordinary_page_is_untouched(self):
        out = self.fold([{"p": "about/faq.html", "pv": 9, "c": 9}])
        self.assertEqual(out, {"about/faq.html": 9})

    def test_shares_count_the_current_docs_not_every_version(self):
        """The default field is the one comparable with /en/stable/."""
        rows = [{"p": "about/faq.html", "pv": 100, "c": 60}]
        self.assertEqual(self.fold(rows), {"about/faq.html": 60})
        self.assertEqual(self.fold(rows, "pv"), {"about/faq.html": 100})

    def test_a_rollup_without_the_split_stops_the_run(self):
        """Old files sum every version into one number and cannot be repaired
        after the fact, so they must fail loudly rather than read as a share."""
        with self.assertRaises(SystemExit):
            self.fold([{"p": "about/faq.html", "pv": 9}])


class TestCurrentEdition(unittest.TestCase):
    """Which version slug counts as "the current docs", asked by exclusion.

    English publishes `stable` and `latest`; the translations publish neither,
    and their current edition is `4.x`. Only the pinned releases look alike
    across languages — they are spelled as numbers.
    """

    def pinned(self, version):
        return bool(fetch.PINNED_VERSION.match(version))

    def test_rolling_slugs_are_the_current_docs(self):
        for version in ("stable", "latest", "4.x", "5.x"):
            self.assertFalse(self.pinned(version), version)

    def test_pinned_releases_are_excluded(self):
        for version in ("4.7", "4.5", "3.6", "3", "4.0.1"):
            self.assertTrue(self.pinned(version), version)


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

    def test_word_count_skips_code_directives_and_underlines(self):
        text = (
            "Page title\n"
            "==========\n"
            "\n"
            "One two three.\n"
            "\n"
            ".. code-block:: gdscript\n"
            "\n"
            "    var ignored = 1\n"
            "\n"
            "Four five.\n"
        )
        # "Page title" (2) + "One two three." (3) + "Four five." (2)
        self.assertEqual(inventory.word_count(text), 7)

    def test_word_count_keeps_admonition_prose(self):
        """Notes and warnings render as callouts but are ordinary prose, and
        godot-docs uses them for about a tenth of the manual."""
        text = "Before.\n\n.. note::\n\n    Real prose in a note.\n\nAfter.\n"
        self.assertEqual(inventory.word_count(text), 7)

    def test_word_count_skips_code_nested_in_an_admonition(self):
        text = (".. note::\n\n    Prose line.\n\n"
                "    .. code-block:: gdscript\n\n        var y = 2\n")
        self.assertEqual(inventory.word_count(text), 2)

    def test_word_count_skips_literal_blocks(self):
        """A paragraph ending in '::' introduces indented code, which is not a
        directive and so was previously counted as prose."""
        self.assertEqual(
            inventory.word_count("Run this::\n\n    godot --headless\n\nDone.\n"), 3)

    def test_word_count_ignores_table_and_list_markup(self):
        table = "+------+------+\n| en   | Eng  |\n+------+------+\n"
        self.assertEqual(inventory.word_count(table), 2)
        self.assertEqual(inventory.word_count("- one\n- two\n"), 2)

    def test_word_count_unwraps_roles(self):
        # "See the manual now." — the role markup contributes no words of its own.
        self.assertEqual(inventory.word_count("See :ref:`the manual` now.\n"), 4)

    def test_word_count_counts_non_latin_words(self):
        """The rule is 'token contains a letter', via str.isalpha(), so quoted
        non-Latin text in the i18n pages is prose rather than punctuation."""
        self.assertEqual(inventory.word_count("Sample: こんにちは and θ here.\n"), 5)

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


class TestChurnParsing(unittest.TestCase):
    """Line churn from `git log --numstat -M`.

    Rename handling is the part with teeth: the manual moved a directory during
    the window this report covers, and a rename read as a path of its own puts a
    live page's history under a path that no longer exists.
    """

    def test_rename_forms_resolve_to_the_new_path(self):
        self.assertEqual(inventory.numstat_path("tutorials/a.rst"),
                         "tutorials/a.rst")
        self.assertEqual(inventory.numstat_path("old.rst => new.rst"), "new.rst")
        self.assertEqual(
            inventory.numstat_path("tutorials/{cpp => engine_api}/core.rst"),
            "tutorials/engine_api/core.rst")

    def test_rename_out_of_a_directory_leaves_no_empty_segment(self):
        """One brace side is empty when a file moves to or from the top level,
        which leaves a doubled separator in the path git prints."""
        self.assertEqual(inventory.numstat_path("{tutorials/cpp => }/core.rst"),
                         "core.rst")
        self.assertEqual(inventory.numstat_path("{ => engine_details}/api.rst"),
                         "engine_details/api.rst")

    def log(self, *commits):
        """A --numstat log in the shape parse_churn reads: each commit a NUL
        marker line with its date, then one line per file."""
        out = []
        for date, files in commits:
            out.append("\x00" + date)
            out.append("")
            out.extend(files)
        return "\n".join(out) + "\n"

    def test_sums_added_and_deleted_per_month(self):
        churn = inventory.parse_churn(self.log(
            ("2026-06-24T10:00:00+02:00", ["10\t2\ttutorials/a.rst"]),
            ("2026-06-02T10:00:00+02:00", ["1\t0\ttutorials/a.rst"]),
            ("2026-05-30T10:00:00+02:00", ["4\t40\ttutorials/a.rst"]),
        ))
        self.assertEqual(churn["tutorials/a.rst"]["a"], 15)
        self.assertEqual(churn["tutorials/a.rst"]["d"], 42)
        self.assertEqual(churn["tutorials/a.rst"]["e"], 3)
        self.assertEqual(churn["tutorials/a.rst"]["m"],
                         {"2026-06": 13, "2026-05": 44})

    def test_pure_rename_is_not_an_edit(self):
        """A moved page whose text did not change contributes no churn: -M
        reports it as 0/0, and counting it would make a directory move look
        like a month of writing."""
        churn = inventory.parse_churn(self.log(
            ("2026-06-24T10:00:00+02:00", ["0\t0\t{old => new}/a.rst"]),
        ))
        self.assertEqual(churn, {})

    def test_rename_with_edits_lands_on_the_new_path(self):
        churn = inventory.parse_churn(self.log(
            ("2026-06-24T10:00:00+02:00", ["6\t1\t{old => new}/a.rst"]),
        ))
        self.assertEqual(list(churn), ["new/a.rst"])
        self.assertEqual(churn["new/a.rst"]["a"], 6)

    def test_binary_and_non_rst_lines_are_skipped(self):
        churn = inventory.parse_churn(self.log(
            ("2026-06-24T10:00:00+02:00",
             ["-\t-\timg/shot.png", "3\t1\tconf.py", "2\t2\ta.rst"]),
        ))
        self.assertEqual(list(churn), ["a.rst"])

    def test_window_is_anchored_on_head_not_today(self):
        """Re-running against an unchanged clone must reproduce the same window,
        so the start is derived from HEAD's commit date."""
        head = int(datetime.datetime(2026, 6, 24, 12, tzinfo=datetime.timezone.utc)
                   .timestamp())
        self.assertEqual(inventory.churn_start(head), "2024-07-01")

    def test_window_snaps_to_a_period_boundary(self):
        """Whichever month HEAD lands in, the window opens on a quarter start,
        so only the newest column is ever a part-period."""
        for day in (1, 30):
            for month in (4, 5, 6):
                head = int(datetime.datetime(2026, month, day,
                                             tzinfo=datetime.timezone.utc).timestamp())
                self.assertEqual(inventory.churn_start(head), "2024-07-01",
                                 f"2026-{month:02d}-{day:02d}")
        # The first month of a quarter opens the window a quarter later.
        head = int(datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)
                   .timestamp())
        self.assertEqual(inventory.churn_start(head), "2024-10-01")


class TestChurnRollup(unittest.TestCase):
    """build_churn: the section grid the page draws."""

    def inventory(self):
        return {
            "churnSince": "2026-04-01",
            "churnHead": int(datetime.datetime(
                2026, 6, 10, tzinfo=datetime.timezone.utc).timestamp()),
            "pages": {
                "/en/stable/tutorials/a.html":
                    {"src": "tutorials/a.rst", "words": 10, "edited": 1,
                     "generated": False},
                "/en/stable/tutorials/2d/b.html":
                    {"src": "tutorials/2d/b.rst", "words": 10, "edited": 1,
                     "generated": False},
                "/en/stable/":
                    {"src": "index.rst", "words": 10, "edited": 1,
                     "generated": False},
                "/en/stable/classes/class_node.html":
                    {"src": "classes/class_node.rst", "words": 10, "edited": 1,
                     "generated": True},
            },
            "churn": {
                "tutorials/a.rst": {"a": 30, "d": 5, "e": 2,
                                    "m": {"2026-04": 20, "2026-06": 15}},
                "tutorials/2d/b.rst": {"a": 4, "d": 4, "e": 1,
                                       "m": {"2026-06": 8}},
                "index.rst": {"a": 1, "d": 0, "e": 1, "m": {"2026-05": 1}},
                "classes/class_node.rst": {"a": 900, "d": 900, "e": 9,
                                           "m": {"2026-05": 1800}},
            },
        }

    def index(self, inv):
        """Row indices as build_docs assigns them: pages in URL order."""
        return {url: i for i, url in enumerate(sorted(inv["pages"]))}

    def build(self):
        inv = self.inventory()
        return aggregate.build_churn(inv, self.index(inv)), inv

    def test_months_cover_the_window_including_silent_ones(self):
        self.assertEqual(aggregate.churn_months(self.inventory()),
                         ["2026-04", "2026-05", "2026-06"])

    def test_months_fold_into_one_period(self):
        """The fixture's window is one calendar quarter, so all of it is one
        column and a page edited in two of its months shows as their sum."""
        out, inv = self.build()
        row = self.index(inv)["/en/stable/tutorials/a.html"]
        self.assertEqual([p["label"] for p in out["periods"]], ["Q2 26"])
        self.assertEqual(out["series"][row], {0: 35})

    def test_only_the_newest_period_is_marked_partial(self):
        """It ends at the docs commit rather than at its own end. Read as a full
        column it looks like work falling off."""
        out, _ = self.build()
        self.assertEqual([p["partial"] for p in out["periods"]], [True])

    def test_periods_with_no_edits_are_absent_rather_than_zero(self):
        out, inv = self.build()
        row = self.index(inv)["/en/stable/tutorials/2d/b.html"]
        self.assertEqual(out["series"][row], {0: 8})

    def test_pages_with_no_churn_get_no_entry(self):
        inv = self.inventory()
        inv["pages"]["/en/stable/quiet.html"] = {
            "src": "quiet.rst", "words": 10, "edited": 1, "generated": False}
        out = aggregate.build_churn(inv, self.index(inv))
        self.assertNotIn(self.index(inv)["/en/stable/quiet.html"], out["series"])

    def test_generated_pages_keep_their_series(self):
        """The grid follows the path bar's class-reference choice like every
        other chart, so the data has to be there when it is switched on."""
        out, inv = self.build()
        row = self.index(inv)["/en/stable/classes/class_node.html"]
        self.assertEqual(out["series"][row], {0: 1800})

    def test_periods_break_on_the_calendar_not_on_the_window(self):
        """Boundaries that moved with the window would give the same column a
        different span on every run, and no two runs could be compared."""
        months = ["2025-05", "2025-06", "2025-07", "2025-08"]
        periods = aggregate.churn_periods(months, months[-1])
        self.assertEqual([p["months"] for p in periods],
                         [["2025-05", "2025-06"], ["2025-07", "2025-08"]])
        self.assertEqual([p["label"] for p in periods], ["Q2 25", "Q3 25"])
        self.assertEqual([p["partial"] for p in periods], [False, True])

    def test_period_labels_name_the_quarter(self):
        """Short, because sixteen of them share the width of the page."""
        self.assertEqual(
            aggregate.period_label(["2025-07", "2025-08", "2025-09"]), "Q3 25")
        self.assertEqual(aggregate.period_label(["2026-01"]), "Q1 26")
        self.assertEqual(aggregate.period_label(["2022-10", "2022-11"]), "Q4 22")


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

    def test_the_not_found_template_is_not_a_page(self):
        """404.rst is what Read the Docs serves *instead of* a missing URL, so
        its own pageviews are structurally zero and its body is raw HTML. As a
        page it was an empty bar in the section chart and the one exception to
        "every page was read at least once"."""
        self.assertNotIn(self.docs["prefix"] + "404.html", self.docs["paths"])

    def test_funnel_paths_exist(self):
        known = set(self.docs["paths"])
        for group in self.docs["funnel"]:
            self.assertIn(group["landing"], known)
            for url in group["steps"]:
                self.assertIn(url, known)

    def test_funnel_follows_the_toctree_order(self):
        # The introduction must come before the 2D game, whatever the traffic.
        order = [g["landing"] for g in self.docs["funnel"]]
        intro = order.index("/en/stable/getting_started/introduction/index.html")
        game = order.index("/en/stable/getting_started/first_2d_game/index.html")
        self.assertLess(intro, game)

    def test_funnel_steps_sit_under_their_landing_page(self):
        """The chart divides every step by its landing page's pageviews, and
        labels each series by the landing page's folder. A step from a
        different folder would be charted against a denominator it has nothing
        to do with, under someone else's name."""
        for group in self.docs["funnel"]:
            folder = group["landing"].rsplit("/", 1)[0]
            self.assertTrue(group["landing"].endswith("/index.html"),
                            group["landing"])
            for url in group["steps"]:
                self.assertEqual(url.rsplit("/", 1)[0], folder)
                self.assertNotEqual(url, group["landing"])

    def test_funnel_covers_the_whole_getting_started_guide(self):
        """Read from the toctrees, so a page added to getting_started/ without
        being listed in one would be missing from the chart while the page's
        own prose calls the guide a third of all reading."""
        charted = {g["landing"] for g in self.docs["funnel"]}
        charted.update(u for g in self.docs["funnel"] for u in g["steps"])
        under = {p for p in self.docs["paths"] if "/getting_started/" in p}
        self.assertEqual(under - charted, set())

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

    def test_row_schema_is_the_documented_ten_columns(self):
        """app.js indexes rows positionally; a widened row silently shifts every
        column it reads."""
        for row in self.docs["rows"]:
            self.assertEqual(len(row), 10)

    def test_every_page_has_a_length_to_shade_against(self):
        """The churn grid divides by it. A zero would put every edited period of
        that row at full intensity."""
        for row in self.docs["rows"]:
            self.assertGreater(row[9], 0, self.docs["paths"][row[0]])

    def test_row_churn_is_a_total_and_a_net(self):
        """The page recovers added and deleted from the pair, so the net can
        never exceed the total and the two must share a parity."""
        for row in self.docs["rows"]:
            total, net = row[7], row[8]
            self.assertGreaterEqual(total, 0)
            self.assertLessEqual(abs(net), total)
            self.assertEqual((total - net) % 2, 0)

    def test_churn_series_sum_to_each_row_total(self):
        """The grid rolls the monthly series up itself and the table prints the
        row total. Two numbers for the same thing have to agree."""
        churn = self.docs["churn"]
        rows = self.docs["rows"]
        for index, series in churn["series"].items():
            self.assertEqual(sum(series.values()), rows[int(index)][7])

    def test_churn_periods_are_addressable(self):
        """Quarters are referenced by position, so a cell outside the window
        would land on the wrong column or on nothing."""
        span = range(len(self.docs["churn"]["periods"]))
        for series in self.docs["churn"]["series"].values():
            for period in series:
                self.assertIn(int(period), span)

    def test_churn_periods_cover_the_window_once_each(self):
        """Every month of the window belongs to exactly one column, or the grid
        double-counts a burst or loses one."""
        churn = self.docs["churn"]
        self.assertEqual(len(churn["periods"]), len(churn["months"]))
        flat = [month for group in churn["months"] for month in group]
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual(flat, sorted(flat))

    def test_every_page_with_churn_has_a_series(self):
        """A row carrying a total but no series is a page the grid would draw as
        never edited while the table beside it says otherwise."""
        series = self.docs["churn"]["series"]
        for i, row in enumerate(self.docs["rows"]):
            self.assertEqual(row[7] > 0, str(i) in series,
                             self.docs["paths"][row[0]])

    def test_sections_series_match_month_count(self):
        for name, series in self.docs["sections"].items():
            self.assertEqual(len(series), len(self.payload["months"]), name)

    def test_listed_urls_are_all_removed_pages(self):
        """The heading promises removed pages. A URL that was never a page, or
        one that is forwarded, would make the table's premise false."""
        for url, pv, removed_ts in self.docs["unforwarded"]:
            self.assertGreater(removed_ts, 0, url)
            self.assertGreater(pv, 0, url)

    def test_stale_kinds_account_for_every_stale_pageview(self):
        """The caption states these as parts of the total. If they do not sum
        to it, the numbers quietly stop adding up."""
        self.assertEqual(sum(self.docs["staleKinds"].values()),
                         self.docs["staleTotal"])
        self.assertEqual(sum(self.docs["staleKindUrls"].values()),
                         self.docs["staleUrls"])

    def test_unforwarded_total_matches_its_kinds(self):
        """The table's own total has to be the buckets it is drawn from, or the
        list and the sentence above it disagree."""
        kinds = self.docs["staleKinds"]
        self.assertEqual(
            self.docs["unforwardedTotal"],
            kinds.get("noRedirect", 0) + kinds.get("removedDuring", 0))

    def test_redirects_were_actually_loaded(self):
        """If the sparse checkout stops fetching redirects.csv, every forwarded
        page silently reappears as a dead link."""
        self.assertGreater(self.docs["redirectCount"], 100)
        self.assertGreater(self.docs["staleKinds"].get("redirected", 0), 0)

    def test_window_start_matches_the_reported_period(self):
        """app.js marks a removal as "during" by comparing against it, so a
        wrong windowStart silently reclassifies every row."""
        import datetime
        first = datetime.date.fromisoformat(self.payload["period"][0])
        self.assertEqual(
            datetime.datetime.fromtimestamp(
                self.docs["windowStart"], datetime.timezone.utc).date(),
            first)


class TestRedirects(unittest.TestCase):
    """`_static/redirects.csv` is applied by 404.rst in the reader's browser,
    so a forwarded URL still records a pageview at the path it left behind.
    Matching it wrong turns "reader arrived fine" into "reader hit a dead end",
    which is the whole distinction this table rests on.
    """

    CSV = ("source,destination\n"
           "/about/index.html,/index.html\n"
           "/development/compiling/for_web.html,/contributing/compiling.html\n"
           "\n"
           "/legacy.html,https://example.com/moved\n")

    def redirects(self):
        return dict(inventory.parse_redirects(self.CSV))

    def test_header_and_blank_lines_are_not_redirects(self):
        rows = inventory.parse_redirects(self.CSV)
        self.assertEqual(len(rows), 3)
        self.assertNotIn("source", dict(rows))

    def test_matches_on_any_path_suffix(self):
        """404.rst uses pathname.endsWith(source), so a version prefix does not
        prevent a match."""
        self.assertEqual(
            aggregate.redirect_for("/en/stable/about/index.html", self.redirects()),
            "/index.html")

    def test_matches_a_suffix_below_the_version_root(self):
        """The compiling pages moved under contributing/, so the stored source
        is a tail of the URL people actually request."""
        self.assertEqual(
            aggregate.redirect_for(
                "/en/stable/contributing/development/compiling/for_web.html",
                self.redirects()),
            "/contributing/compiling.html")

    def test_unrelated_url_is_not_forwarded(self):
        self.assertIsNone(
            aggregate.redirect_for("/en/stable/tutorials/2d/index.html",
                                   self.redirects()))

    def test_a_longer_name_ending_in_a_source_is_not_a_match(self):
        """Suffix matching must break on "/" — "my_legacy.html" ends with
        "legacy.html" as text but is a different page."""
        self.assertIsNone(
            aggregate.redirect_for("/en/stable/my_legacy.html", self.redirects()))
        self.assertEqual(
            aggregate.redirect_for("/en/stable/legacy.html", self.redirects()),
            "https://example.com/moved")

    def test_absent_redirects_file_leaves_every_page_unforwarded(self):
        """A sparse checkout that stops fetching the CSV must not quietly
        reclassify the whole table."""
        self.assertIsNone(aggregate.redirect_for("/en/stable/about/index.html", {}))


class TestRemovalDates(unittest.TestCase):
    """A URL with no source file is not automatically a stale bookmark.

    Traffic to a page removed part-way through the window is ordinary reading,
    not people arriving at a dead link, and the two must not be presented alike.
    """

    def test_removals_take_the_most_recent_deletion(self):
        log = "\n1700000000\n\nold/page.rst\n\n1600000000\n\nold/page.rst\n"
        self.assertEqual(inventory.parse_removals(log),
                         {"old/page.rst": 1700000000})

    def test_removals_ignore_non_rst_paths(self):
        log = "\n1700000000\n\nconf.py\nimg/screenshot.png\ngone.rst\n"
        self.assertEqual(inventory.parse_removals(log), {"gone.rst": 1700000000})

    def test_a_url_that_was_never_a_page_is_not_called_removed(self):
        start = aggregate.month_start_ts("2026-07")
        self.assertEqual(aggregate.stale_kind(None, start, None), "neverPage")
        self.assertEqual(aggregate.stale_kind(0, start, None), "neverPage")

    def test_removal_during_the_window_is_kept_apart(self):
        start = aggregate.month_start_ts("2026-07")
        self.assertEqual(aggregate.stale_kind(start - 1, start, None),
                         "noRedirect")
        self.assertEqual(aggregate.stale_kind(start, start, None),
                         "removedDuring")
        self.assertEqual(aggregate.stale_kind(start + 86400, start, None),
                         "removedDuring")

    def test_a_forwarded_page_is_never_called_unforwarded(self):
        """The reader reached their destination; the pageview at the old URL is
        an artifact of the redirect running in the browser."""
        start = aggregate.month_start_ts("2026-07")
        for removed_ts in (None, start - 1, start + 1):
            self.assertEqual(
                aggregate.stale_kind(removed_ts, start, "/somewhere.html"),
                "redirected")

    def test_month_start_is_the_first_of_the_month(self):
        import datetime
        ts = aggregate.month_start_ts("2026-07")
        self.assertEqual(
            datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).date(),
            datetime.date(2026, 7, 1))


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
