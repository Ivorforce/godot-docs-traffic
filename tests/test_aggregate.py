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
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import aggregate  # noqa: E402
import fetch  # noqa: E402
import inventory  # noqa: E402
import l10n  # noqa: E402
import plausible  # noqa: E402
import store  # noqa: E402


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

    def by_lang(self, rows, field="c"):
        original = aggregate.store.read
        aggregate.store.read = lambda kind, month: rows
        try:
            return {k: dict(v) for k, v in
                    aggregate.load_i18n_by_lang("2026-07", field).items()}
        finally:
            aggregate.store.read = original

    def test_landing_page_folds_onto_index(self):
        out = self.fold([{"p": "", "l": "ja", "pv": 40, "c": 30},
                         {"p": "index.html", "l": "ja", "pv": 5, "c": 5}])
        self.assertEqual(out, {"index.html": 35})

    def test_directory_folds_onto_index(self):
        out = self.fold([{"p": "tutorials/2d/", "l": "de", "pv": 7, "c": 7},
                         {"p": "tutorials/2d/index.html", "l": "de",
                          "pv": 3, "c": 3}])
        self.assertEqual(out, {"tutorials/2d/index.html": 10})

    def test_ordinary_page_is_untouched(self):
        out = self.fold([{"p": "about/faq.html", "l": "fr", "pv": 9, "c": 9}])
        self.assertEqual(out, {"about/faq.html": 9})

    def test_shares_count_the_current_docs_not_every_version(self):
        """The default field is the one comparable with /en/stable/."""
        rows = [{"p": "about/faq.html", "l": "de", "pv": 100, "c": 60}]
        self.assertEqual(self.fold(rows), {"about/faq.html": 60})
        self.assertEqual(self.fold(rows, "pv"), {"about/faq.html": 100})

    def test_languages_sum_for_the_english_report(self):
        """The English page wants one number per page, not fifteen."""
        out = self.fold([{"p": "about/faq.html", "l": "de", "pv": 9, "c": 9},
                         {"p": "about/faq.html", "l": "ja", "pv": 4, "c": 4}])
        self.assertEqual(out, {"about/faq.html": 13})

    def test_languages_stay_apart_for_the_translation_view(self):
        out = self.by_lang([{"p": "about/faq.html", "l": "de", "pv": 9, "c": 9},
                            {"p": "about/faq.html", "l": "ja", "pv": 4, "c": 4},
                            {"p": "", "l": "ja", "pv": 6, "c": 6}])
        self.assertEqual(out, {"de": {"about/faq.html": 9},
                               "ja": {"about/faq.html": 4, "index.html": 6}})

    def test_the_same_folding_applies_per_language(self):
        """A landing page must key as index.html on both paths through the data,
        or the two views would disagree about the largest key in the file."""
        rows = [{"p": "", "l": "ja", "pv": 6, "c": 6}]
        self.assertEqual(self.fold(rows), {"index.html": 6})
        self.assertEqual(self.by_lang(rows), {"ja": {"index.html": 6}})

    def test_the_summed_shape_is_still_read(self):
        """--by-language is off by default, so the ordinary file has no `l` and
        the English report must read it exactly as before."""
        out = self.fold([{"p": "about/faq.html", "pv": 100, "c": 60},
                         {"p": "", "pv": 40, "c": 30}])
        self.assertEqual(out, {"about/faq.html": 60, "index.html": 30})

    def test_a_rollup_without_the_version_split_stops_the_run(self):
        """Old files sum every version into one number and cannot be repaired
        after the fact, so they must fail loudly rather than read as a share."""
        with self.assertRaises(SystemExit):
            self.fold([{"p": "about/faq.html", "l": "de", "pv": 9}])

    def test_asking_per_language_of_a_summed_file_stops_the_run(self):
        """A sum cannot be split back into what it summed, so the caller is sent
        to the flag rather than handed thirteen languages collapsed into one."""
        with self.assertRaises(SystemExit):
            self.by_lang([{"p": "about/faq.html", "pv": 9, "c": 9}])


class TestStoreOrdering(unittest.TestCase):
    """Re-running a fetch on unchanged data must produce identical bytes.

    Once a kind stores more than one row per path, sorting on the path alone
    leaves the ties ordered by whatever the API returned, and an unchanged month
    still shows up as a diff.
    """

    def written(self, rows, **kw):
        original = store.RAW_DIR
        with tempfile.TemporaryDirectory() as tmp:
            store.RAW_DIR = Path(tmp)
            try:
                return store.write("k", rows, **kw).read_text()
            finally:
                store.RAW_DIR = original

    def test_compound_key_breaks_ties_deterministically(self):
        rows = [{"p": "a.html", "l": "ja", "pv": 1},
                {"p": "a.html", "l": "de", "pv": 2}]
        out = self.written(rows, key=("p", "l"))
        self.assertEqual(out, self.written(list(reversed(rows)), key=("p", "l")))
        self.assertEqual([json.loads(l)["l"] for l in out.splitlines()],
                         ["de", "ja"])

    def test_a_single_key_is_still_accepted(self):
        out = self.written([{"p": "b.html"}, {"p": "a.html"}])
        self.assertEqual([json.loads(l)["p"] for l in out.splitlines()],
                         ["a.html", "b.html"])


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
        self.assertEqual([p["label"] for p in out["periods"]], ["Q2 '26"])
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
        self.assertEqual([p["label"] for p in periods], ["Q2 '25", "Q3 '25"])
        self.assertEqual([p["partial"] for p in periods], [False, True])

    def test_period_labels_name_the_quarter(self):
        """Short, because sixteen of them share the width of the page."""
        self.assertEqual(
            aggregate.period_label(["2025-07", "2025-08", "2025-09"]), "Q3 '25")
        self.assertEqual(aggregate.period_label(["2026-01"]), "Q1 '26")
        self.assertEqual(aggregate.period_label(["2022-10", "2022-11"]), "Q4 '22")


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
        own prose calls the guide a third of all pageviews."""
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
        self.assertGreater(self.docs["staleKinds"].get("forwarded", 0), 0)

    def test_edition_series_cover_every_month(self):
        """The stack is drawn against the shared month labels by index, so a
        short series would silently shift a release's history."""
        for entry in self.docs["editions"]:
            self.assertEqual(len(entry["views"]), len(self.payload["months"]),
                             entry["edition"])

    def test_the_current_docs_lead_the_stack(self):
        """Reading order, not size order: the stack is meant to read like the
        release history."""
        self.assertEqual(self.docs["editions"][0]["edition"], "stable")

    def test_the_remainder_is_last_if_it_is_there(self):
        editions = [e["edition"] for e in self.docs["editions"]]
        if "other" in editions:
            self.assertEqual(editions[-1], "other")

    def test_no_edition_month_carries_a_loop(self):
        """The chart excludes looping pageviews, and April 2026 is why: one URL
        fired 7.2M of them, four times the site's ordinary month. If the
        exclusion ever stops working, the largest month is many times the
        smallest instead of within a fifth of it."""
        totals = [sum(e["views"][i] for e in self.docs["editions"])
                  for i in range(len(self.payload["months"]))]
        self.assertLess(max(totals), 1.5 * min(totals))

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


class TestRedirectWalk(unittest.TestCase):
    """Where a forwarded reader actually ends up.

    `redirect_for` answers one hop. This follows the hops as the browser does,
    because 404.rst runs *on* the 404 page: a destination that is not a page
    either serves 404.rst again, which consults the same file again. A rule
    whose destination is a suffix of its own source therefore matches itself,
    and the loop fires a pageview per pass — which is how one URL took 81% of
    April 2026 on a docs site whose reading did not move.
    """

    PAGES = {"/en/stable/engine_details/file_formats/index.html": {},
             "/en/stable/tutorials/ui/size_and_anchors.html": {}}

    def walk(self, url, rules):
        return aggregate.redirect_walk(url, dict(rules), self.PAGES)

    def test_a_rule_landing_on_a_page_is_not_a_cycle(self):
        self.assertEqual(
            self.walk("/en/stable/tutorials/gui/size_and_anchors.html",
                      [("/tutorials/gui/size_and_anchors.html",
                        "/tutorials/ui/size_and_anchors.html")]),
            ("page", "/en/stable/tutorials/ui/size_and_anchors.html"))

    def test_hops_are_followed_to_the_page(self):
        """Two rules in a row are ordinary: a page that moved twice."""
        self.assertEqual(
            self.walk("/en/stable/learning/features/gui/size_and_anchors.html",
                      [("/learning/features/gui/size_and_anchors.html",
                        "/tutorials/gui/size_and_anchors.html"),
                       ("/tutorials/gui/size_and_anchors.html",
                        "/tutorials/ui/size_and_anchors.html")]),
            ("page", "/en/stable/tutorials/ui/size_and_anchors.html"))

    def test_a_destination_matching_its_own_source_loops(self):
        """The real shape: the destination carries a `development/` segment the
        tree does not have, and ends with the source it came from."""
        rules = [("/development/file_formats/index.html",
                  "/engine_details/development/file_formats/index.html")]
        self.assertEqual(
            self.walk("/en/stable/development/file_formats/index.html", rules),
            ("cycle",
             "/en/stable/engine_details/development/file_formats/index.html"))

    def test_a_loop_is_found_from_either_end(self):
        """Readers reach the destination directly — it is the URL in the traffic
        data — so entering the cycle part-way through must be caught too."""
        rules = [("/development/file_formats/index.html",
                  "/engine_details/development/file_formats/index.html")]
        kind, _ = self.walk(
            "/en/stable/engine_details/development/file_formats/index.html", rules)
        self.assertEqual(kind, "cycle")

    def test_a_longer_loop_is_still_a_loop(self):
        rules = [("/a.html", "/b.html"), ("/b.html", "/a.html")]
        self.assertEqual(self.walk("/en/stable/a.html", rules)[0], "cycle")

    def test_a_destination_that_is_not_a_page_and_not_forwarded_is_dead(self):
        """Broken but finite: the reader gets a 404 and stops. A cycle is the
        subset that costs pageviews, so the two must not be reported alike."""
        self.assertEqual(
            self.walk("/en/stable/gone.html", [("/gone.html", "/nowhere.html")]),
            ("dead", "/en/stable/nowhere.html"))

    def test_an_offsite_destination_ends_the_walk(self):
        """The reader left; nothing here can follow them, and prefixing an
        absolute URL with /en/stable/ would invent a path."""
        self.assertEqual(
            self.walk("/en/stable/legacy.html",
                      [("/legacy.html", "https://example.com/moved")]),
            ("external", "https://example.com/moved"))

    def test_a_self_referencing_rule_for_a_live_page_never_loops(self):
        """A rule pointing at itself is only a loop where the page is missing:
        the server answers a live URL with the page, and 404.rst — which is
        what applies these rules — never runs."""
        rules = {"/tutorials/ui/size_and_anchors.html":
                 "/tutorials/ui/size_and_anchors.html"}
        self.assertEqual(aggregate.redirect_cycles(rules, self.PAGES), [])

    def test_a_rule_forwarding_to_a_missing_page_that_rematches_is_flagged(self):
        rules = {"/development/file_formats/index.html":
                 "/engine_details/development/file_formats/index.html"}
        self.assertEqual(
            [source for source, _ in
             aggregate.redirect_cycles(rules, self.PAGES)],
            ["/development/file_formats/index.html"])

    def test_looping_pageviews_are_counted_from_the_traffic(self):
        """A rule nobody reaches costs nothing, so the figure comes from the
        URLs readers asked for rather than from the rule set."""
        rules = {"/development/file_formats/index.html":
                 "/engine_details/development/file_formats/index.html"}
        current = {
            "/en/stable/engine_details/development/file_formats/index.html": 7000,
            "/en/stable/engine_details/file_formats/index.html": 300,  # a page
            "/en/stable/tutorials/ui/size_and_anchors.html": 50,
        }
        views, hits = aggregate.cycle_views(current, rules, self.PAGES)
        self.assertEqual(views, 7000)
        self.assertEqual([url for url, _ in hits],
                         ["/en/stable/engine_details/development/"
                          "file_formats/index.html"])


class TestEditions(unittest.TestCase):
    """The one chart drawn from more than one month.

    Everything else on the page is `/en/stable/` in the newest month; this is
    where the other fifth of English reading is, and where a release's tail
    after it stops being current can be measured.
    """

    PAGES = {"/en/stable/tutorials/2d/index.html": {}}
    RULES = {"/development/file_formats/index.html":
             "/engine_details/development/file_formats/index.html"}

    def build(self, rows_by_month, **kw):
        original = aggregate.store.read
        aggregate.store.read = lambda kind, month: rows_by_month[month]
        try:
            return aggregate.build_editions(sorted(rows_by_month), self.RULES,
                                            self.PAGES, **kw)
        finally:
            aggregate.store.read = original

    def test_editions_are_split_and_months_kept_in_order(self):
        out = self.build({
            "2026-06": [{"p": "/en/stable/tutorials/2d/index.html", "pv": 10},
                        {"p": "/en/4.6/tutorials/2d/index.html", "pv": 4}],
            "2026-07": [{"p": "/en/stable/tutorials/2d/index.html", "pv": 12}],
        })
        self.assertEqual(out, [{"edition": "stable", "views": [10, 12]},
                               {"edition": "4.6", "views": [4, 0]}])

    def test_looping_pageviews_are_left_out(self):
        """One looping URL fired 7.2M pageviews in April 2026, four times the
        site's ordinary month. Left in, the chart is that URL and nothing
        else."""
        out = self.build({"2026-07": [
            {"p": "/en/stable/tutorials/2d/index.html", "pv": 10},
            {"p": "/en/stable/engine_details/development/file_formats/index.html",
             "pv": 7000000},
        ]})
        self.assertEqual(out, [{"edition": "stable", "views": [10]}])

    def test_a_loop_is_excluded_on_every_edition(self):
        """redirects.csv ships in every build, so the loop is a property of the
        path rather than of the edition serving it."""
        out = self.build({"2026-07": [
            {"p": "/en/4.6/engine_details/development/file_formats/index.html",
             "pv": 500000},
            {"p": "/en/4.6/tutorials/2d/index.html", "pv": 7},
        ]})
        self.assertEqual(out, [{"edition": "4.6", "views": [7]}])

    def test_the_tail_is_summed_rather_than_dropped(self):
        """The bars have to stay the whole of English reading, or the flat
        total the chart is half about stops being checkable."""
        out = self.build({"2026-07": [
            {"p": "/en/stable/a.html", "pv": 100},
            {"p": "/en/3.5/a.html", "pv": 5},
            {"p": "/en/3.4/a.html", "pv": 3},
        ]}, keep=1)
        self.assertEqual(out, [{"edition": "stable", "views": [100]},
                               {"edition": "other", "views": [8]}])

    def test_a_malformed_path_is_not_an_edition(self):
        out = self.build({"2026-07": [{"p": "/robots.txt", "pv": 9},
                                      {"p": "/en/stable/a.html", "pv": 4}]})
        self.assertEqual(out, [{"edition": "stable", "views": [4]}])

    def test_edition_split_keeps_the_path_below_it(self):
        self.assertEqual(aggregate.edition_of("/en/stable/tutorials/2d/index.html"),
                         ("stable", "tutorials/2d/index.html"))
        self.assertEqual(aggregate.edition_of("/en/4.6/"), ("4.6", ""))
        self.assertEqual(aggregate.edition_of("/de/stable/a.html")[0], None)

    def test_releases_are_ordered_newest_first_after_the_rolling_ones(self):
        """Sorted as strings, 4.10 would come before 4.9 and a release would
        move up the stack as the numbering passed ten."""
        editions = ["4.9", "latest", "4.10", "stable", "4.4"]
        self.assertEqual(sorted(editions, key=aggregate.edition_order),
                         ["stable", "latest", "4.10", "4.9", "4.4"])


class TestTrafficAnomalies(unittest.TestCase):
    """The report publishes unattended, and a month can be about something
    other than reading without any figure on the page looking wrong.

    Both arms are needed. The per-page arm names the URL but only sees English;
    the daily arm covers the whole site but only says when.
    """

    # The bar is a share of the month, so every fixture needs a month around it:
    # in a corpus of two pages any move is a large share of the reading, and the
    # threshold would mean nothing. Here 1% is about 1,000 pageviews, which is
    # the proportion the real data has — 17,000 against pages that draw a few
    # hundred.
    REST = "/en/stable/rest-of-the-manual.html"

    def history(self, series):
        """{month: {url: pageviews}} from {url: [pageviews per month]}."""
        length = len(next(iter(series.values())))
        series = dict(series, **{self.REST: [100000] * length})
        months = [f"2026-{i + 1:02d}" for i in range(length)]
        return ({m: {u: v[i] for u, v in series.items()}
                 for i, m in enumerate(months)}, months)

    def flagged(self, series):
        history, months = self.history(series)
        return {url for _, url, _, _ in aggregate.page_anomalies(history, months)}

    def test_a_burst_from_nothing_is_flagged(self):
        """The case this exists for: a URL with no history taking most of a
        month. A ratio rule cannot see it — every ratio from zero is infinite,
        so the same rule fires on every newly written page."""
        self.assertEqual(
            self.flagged({"/en/stable/a.html": [1000, 1000, 1000, 1000],
                          "/en/stable/loop.html": [0, 0, 0, 5000]}),
            {"/en/stable/loop.html"})

    def test_ordinary_movement_is_not_flagged(self):
        self.assertEqual(
            self.flagged({"/en/stable/a.html": [1000, 1100, 900, 1200],
                          "/en/stable/b.html": [500, 400, 600, 550]}),
            set())

    def test_a_small_page_doubling_is_below_the_bar(self):
        """A page moving from 2 to 20 pageviews is a ratio of ten and worth
        nobody's attention. The bar is a share of the month, so size decides."""
        self.assertEqual(
            self.flagged({"/en/stable/a.html": [10000] * 4,
                          "/en/stable/small.html": [2, 2, 2, 20]}),
            set())

    def test_last_months_burst_does_not_raise_this_months_bar(self):
        """The baseline is a median, so a spike inside the history cannot hide
        a second one after it — nor make the month after the spike read as a
        collapse."""
        history, months = self.history(
            {"/en/stable/a.html": [1000, 1000, 1000, 1000, 1000],
             "/en/stable/loop.html": [0, 0, 9000, 0, 9000]})
        self.assertEqual([url for _, url, _, _ in
                          aggregate.page_anomalies(history, months)],
                         ["/en/stable/loop.html"])

    def test_one_month_of_history_flags_nothing(self):
        """A first run has nothing to compare against and must not guess."""
        self.assertEqual(
            aggregate.page_anomalies({"2026-01": {"/en/stable/a.html": 9}},
                                     ["2026-01"]), [])

    def days(self, values, start=1):
        base = datetime.date(2026, 1, 1) + datetime.timedelta(days=start - 1)
        return [((base + datetime.timedelta(days=i)).isoformat(), v)
                for i, v in enumerate(values)]

    def test_a_spike_above_the_trailing_median_is_flagged(self):
        days = self.days([100] * 90 + [400])
        self.assertEqual([d for d, _, _ in aggregate.day_anomalies(days)],
                         [days[-1][0]])

    def test_an_ordinary_day_is_not(self):
        days = self.days([100] * 90 + [140])
        self.assertEqual(aggregate.day_anomalies(days), [])

    def test_no_day_is_judged_before_the_window_is_full(self):
        """A tracker being set up rises 40x in two months, and against a median
        drawn from the ramp every ordinary day after it reads as a spike.
        Waiting for a full window is what keeps that out."""
        ramp = self.days([2000] * 30 + [80000] * 40)
        self.assertEqual(aggregate.day_anomalies(ramp), [])

    def test_a_settled_higher_level_stops_being_a_spike(self):
        """Growth is not an anomaly: once the new level fills the window it is
        the median, and the days stop being flagged."""
        days = self.days([2000] * 45 + [80000] * 200)
        flagged = [d for d, _, _ in aggregate.day_anomalies(days)]
        self.assertTrue(all(d < "2026-06" for d in flagged), flagged)


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
            self.assertEqual(aggregate.stale_kind(removed_ts, start, "page"),
                             "forwarded")

    def test_an_offsite_destination_counts_as_arrival(self):
        """A deliberate target the docs chose, and nothing on this side can
        follow the reader to check — so it is an arrival, not a loose end."""
        start = aggregate.month_start_ts("2026-07")
        self.assertEqual(aggregate.stale_kind(None, start, "external"),
                         "forwarded")

    def test_a_loop_is_not_an_arrival(self):
        """The whole reason the walk replaced a one-hop lookup: forwarded and
        arrived are not the same, and calling a loop forwarded put 87% of that
        bucket in the benign column."""
        start = aggregate.month_start_ts("2026-07")
        self.assertEqual(aggregate.stale_kind(None, start, "cycle"), "looping")

    def test_forwarding_to_another_404_is_kept_apart(self):
        start = aggregate.month_start_ts("2026-07")
        self.assertEqual(aggregate.stale_kind(None, start, "dead"),
                         "forwardedNowhere")

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


class TestPoParsing(unittest.TestCase):
    """The gettext reader behind coverage.

    Its failure mode is silent and total: a parser that reads the language's own
    .po as if it held every source string reports 100% coverage for every page
    in every language, which is what the first attempt at this did.
    """

    def parse(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.po"
            p.write_text(text, encoding="utf-8")
            return list(l10n.po_entries(p))

    HEADER = ('#, fuzzy\nmsgid ""\nmsgstr ""\n'
              '"Project-Id-Version: x\\n"\n"Language: de\\n"\n\n')

    def test_the_header_is_not_an_entry(self):
        self.assertEqual(self.parse(self.HEADER), [])

    def test_the_headers_fuzzy_flag_does_not_leak_onto_the_next_entry(self):
        """Every one of the 6,565 files here opens with a fuzzy-flagged header,
        so a flag that survived the blank line would mark the whole manual
        untranslated."""
        out = self.parse(self.HEADER + 'msgid "Hello there"\nmsgstr "Hallo"\n\n')
        self.assertEqual(out, [("Hello there", "Hallo", False)])

    def test_a_real_fuzzy_entry_is_flagged(self):
        out = self.parse(self.HEADER + '#, fuzzy\nmsgid "One"\nmsgstr "Eins"\n\n')
        self.assertEqual(out[0][2], True)

    def test_multiline_strings_are_joined(self):
        out = self.parse(self.HEADER +
                         'msgid ""\n"one two "\n"three"\nmsgstr "x"\n\n')
        self.assertEqual(out[0][0], "one two three")

    def test_untranslated_entries_keep_an_empty_msgstr(self):
        """A template is exactly this: every msgstr empty. Coverage counts the
        non-empty ones, so the two file kinds read through one parser."""
        out = self.parse(self.HEADER + 'msgid "Untouched"\nmsgstr ""\n\n')
        self.assertEqual(out, [("Untouched", "", False)])

    def test_obsolete_entries_are_skipped(self):
        out = self.parse(self.HEADER + '#~ msgid "Gone"\n#~ msgstr "Weg"\n\n')
        self.assertEqual(out, [])

    def test_plurals_and_context_fail_loudly(self):
        """Neither appears in any of the 6,565 files, so they are refused rather
        than half-handled — a wrong count here is invisible downstream."""
        for line in ('msgid_plural "many"', 'msgctxt "menu"'):
            with self.assertRaises(ValueError):
                self.parse(self.HEADER + f'msgid "One"\n{line}\nmsgstr "x"\n\n')

    def test_the_last_entry_needs_no_trailing_blank_line(self):
        out = self.parse(self.HEADER + 'msgid "Last"\nmsgstr "Letzte"')
        self.assertEqual(out, [("Last", "Letzte", False)])


class TestCoverageWeighting(unittest.TestCase):
    """Coverage is weighted by words, not by message count.

    Short strings get translated first, so a message count overstates progress
    everywhere and worst where the work is left. tutorials/2d/index really is
    four of six messages done and 9% of its words.
    """

    def test_words_use_the_inventory_rule(self):
        self.assertEqual(l10n.words("Physics and movement"), 3)
        self.assertEqual(l10n.words("| --- | === |"), 0)
        self.assertEqual(l10n.words("Node2D res:// 42"), 2)

    def test_headings_and_prose_weigh_differently(self):
        headings = sum(l10n.words(m) for m in
                       ["2D", "Rendering", "Physics and movement", "Tools"])
        prose = l10n.words(
            "Godot includes a dedicated 2D renderer and 2D physics engine, as "
            "well as tools for building tile maps and sprite animations")
        self.assertEqual(headings, 6)
        self.assertLess(headings / (headings + prose), 0.25,
                        "four of six messages, well under a quarter of the words")


class TestLanguageCodes(unittest.TestCase):
    """The translation repository spells languages the way gettext does; the
    docs URLs, and so the traffic data, spell them lowercase with a hyphen."""

    def test_region_codes_normalise_to_the_url_spelling(self):
        self.assertEqual(l10n.lang_code("zh_CN"), "zh-cn")
        self.assertEqual(l10n.lang_code("pt_BR"), "pt-br")

    def test_plain_codes_are_unchanged(self):
        self.assertEqual(l10n.lang_code("de"), "de")

    def test_every_shipped_language_matches_a_code_the_traffic_data_uses(self):
        """Both sides are small and known, so the join can be asserted rather
        than hoped for."""
        seen = {"cs", "de", "es", "fr", "it", "ja", "ko", "pl", "pt-br", "ru",
                "uk", "zh-cn", "zh-tw"}
        self.assertEqual({l10n.lang_code(d) for d in
                          ["cs", "de", "es", "fr", "it", "ja", "ko", "pl",
                           "pt_BR", "ru", "uk", "zh_CN", "zh_TW"]}, seen)


if __name__ == "__main__":
    unittest.main()
