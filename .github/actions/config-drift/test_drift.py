#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""The drift report on repositories held in memory: nothing is fetched."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import drift  # noqa: E402

PIN = "6ce8f730e6c988e0d1826cdf2a8b01af350f114b"
OLD = "f2c8315bf8c606db37fca1c261d54609066caf9c"

MISC = drift.Repository("rhalbersma/xstd-misc", None)
INTS = drift.Repository("rhalbersma/xstd-ints", None)
BITS = drift.Repository("rhalbersma/xstd-bits", "main")
TABULA = drift.Repository("rhalbersma/tabula", None)

FORMAT_OLD = b"BasedOnStyle: LLVM\nIndentWidth: 8\n"
FORMAT_NEW = b"BasedOnStyle: LLVM\nIndentWidth: 8\nBreakInheritanceList: AfterColon\n"

FILES = {
    (MISC, ".clang-format"): drift.Fetched(FORMAT_OLD, "2026-10-04T12:00:00Z"),
    (INTS, ".clang-format"): drift.Fetched(FORMAT_OLD, "2026-10-03T12:00:00Z"),
    (BITS, ".clang-format"): drift.Fetched(FORMAT_NEW, "2026-09-29T12:00:00Z"),
    (TABULA, ".clang-format"): drift.Fetched(None),
    (MISC, ".gitignore"): drift.Fetched(b"build/\n", "2026-01-01T00:00:00Z"),
    (INTS, ".gitignore"): drift.Fetched(b"build/\n", "2026-02-01T00:00:00Z"),
    (BITS, ".gitignore"): drift.Fetched(b"build/\n", "2026-03-01T00:00:00Z"),
    (TABULA, ".gitignore"): drift.Fetched(None, error="HTTP Error 500"),
}

WORKFLOWS = {
    MISC: [f"jobs:\n  gcc:\n    uses: rhalbersma/cpp-ci/.github/workflows/gcc.yml@{PIN} # main after rhalbersma/cpp-ci#104\n"],
    INTS: [
        f"    uses: rhalbersma/cpp-ci/.github/workflows/gcc.yml@{PIN} # v0.15.0\n",
        f"    uses: rhalbersma/cpp-ci/.github/workflows/clang.yml@{OLD}\n",
        f"    uses: someone/cpp-ci/.github/workflows/gcc.yml@{OLD}\n",
    ],
    BITS: [],
    TABULA: "HTTP Error 403",
}


def run(repositories, files, pinned="rhalbersma/cpp-ci"):
    return drift.report(
        repositories, files, pinned,
        lambda r, p: FILES[(r, p)],
        lambda r: WORKFLOWS[r],
    )


class Parsing(unittest.TestCase):
    def test_repositories_take_an_optional_ref(self):
        self.assertEqual(
            drift.parse_repositories("rhalbersma/xstd-misc\n  rhalbersma/xstd-bits@main"),
            [MISC, BITS],
        )

    def test_one_repository_compares_nothing(self):
        with self.assertRaises(ValueError):
            drift.parse_repositories("rhalbersma/xstd-misc")

    def test_a_bare_name_is_refused(self):
        with self.assertRaises(ValueError):
            drift.parse_repositories("xstd-misc xstd-ints")

    def test_a_repository_named_twice_is_refused(self):
        with self.assertRaises(ValueError):
            drift.parse_repositories("rhalbersma/xstd-misc rhalbersma/xstd-misc")

    def test_one_repository_at_two_refs_is_two(self):
        self.assertEqual(len(drift.parse_repositories("o/r@v1 o/r@v2")), 2)

    def test_files_are_required_and_deduplicated(self):
        with self.assertRaises(ValueError):
            drift.parse_files("  ")
        self.assertEqual(drift.parse_files(".clang-format .gitignore .clang-format"), [".clang-format", ".gitignore"])


class Classifying(unittest.TestCase):
    def test_the_newest_change_leads_whatever_the_majority(self):
        _, (report,) = run([MISC, INTS, BITS, TABULA], [".clang-format"])
        self.assertTrue(report.drifted)
        self.assertEqual([v.repositories for v in report.variants], [[MISC, INTS], [BITS]])
        self.assertEqual(report.variants[0].date, "2026-10-04T12:00:00Z")
        self.assertEqual(report.absent, [TABULA])

    def test_an_unreadable_copy_is_not_drift(self):
        _, (report,) = run([MISC, INTS, BITS, TABULA], [".gitignore"])
        self.assertFalse(report.drifted)
        self.assertEqual(report.unreadable, [(TABULA, "HTTP Error 500")])

    def test_absent_beside_present_is_drift(self):
        report = drift.classify("f", {MISC: drift.Fetched(b"x"), TABULA: drift.Fetched(None)})
        self.assertTrue(report.drifted)

    def test_absent_from_all_is_agreement(self):
        report = drift.classify("f", {MISC: drift.Fetched(None), TABULA: drift.Fetched(None)})
        self.assertFalse(report.drifted)


class Pins(unittest.TestCase):
    def test_only_the_pinned_repository_counts(self):
        pins = [pin for text in WORKFLOWS[INTS] for pin in drift.find_pins(text, "rhalbersma/cpp-ci")]
        self.assertEqual(pins, [(PIN, "v0.15.0"), (OLD, "")])

    def test_a_repository_name_is_not_a_pattern(self):
        self.assertEqual(drift.find_pins(f"uses: rhalbersmaXcpp-ci/a@{PIN}", "rhalbersma.cpp-ci"), [])

    def test_a_short_sha_is_not_a_pin(self):
        self.assertEqual(drift.find_pins("uses: rhalbersma/cpp-ci/a@6ce8f73", "rhalbersma/cpp-ci"), [])

    def test_distinct_pins_are_listed_once_each(self):
        self.assertEqual(drift.summarize_pins([(PIN, "v1"), (PIN, "v1"), (OLD, "")]), f"`{PIN[:7]}` v1, `{OLD[:7]}`")
        self.assertEqual(drift.summarize_pins([]), "none")


class Rendering(unittest.TestCase):
    def setUp(self):
        self.text, self.reports = run([MISC, INTS, BITS, TABULA], [".clang-format", ".gitignore"])

    def test_each_file_has_a_row(self):
        self.assertIn("| `.clang-format` | newest, 2026-10-04: `rhalbersma/xstd-misc`, `rhalbersma/xstd-ints`; "
                      "variant, 2026-09-29: `rhalbersma/xstd-bits@main`; absent: `rhalbersma/tabula` |", self.text)
        self.assertIn("| `.gitignore` | all 3 agree; unreadable: `rhalbersma/tabula` (HTTP Error 500) |", self.text)

    def test_each_repository_has_its_pins(self):
        self.assertIn(f"| `rhalbersma/xstd-misc` | `{PIN[:7]}` main after rhalbersma/cpp-ci#104 |", self.text)
        self.assertIn(f"| `rhalbersma/xstd-ints` | `{PIN[:7]}` v0.15.0, `{OLD[:7]}` |", self.text)
        self.assertIn("| `rhalbersma/xstd-bits@main` | none |", self.text)
        self.assertIn("| `rhalbersma/tabula` | unreadable (HTTP Error 403) |", self.text)

    def test_no_pinned_repository_lists_no_pins(self):
        text, _ = run([MISC, INTS], [".gitignore"], pinned="")
        self.assertNotIn("| Repository |", text)

    def test_a_variant_is_shown_against_the_newest(self):
        self.assertIn("--- rhalbersma/xstd-misc/.clang-format\n+++ rhalbersma/xstd-bits@main/.clang-format\n", self.text)
        self.assertIn("\n+BreakInheritanceList: AfterColon\n", self.text)
        self.assertIn("<summary><code>.clang-format</code>: <code>rhalbersma/xstd-bits@main</code> against "
                      "<code>rhalbersma/xstd-misc</code>, <code>rhalbersma/xstd-ints</code></summary>", self.text)

    def test_a_long_diff_is_cut(self):
        many = drift.classify("f", {
            MISC: drift.Fetched(b"".join(b"%d\n" % i for i in range(1000)), "2026-02-01"),
            INTS: drift.Fetched(b"", "2026-01-01"),
        })
        text = drift.render([many], [MISC, INTS], None)
        self.assertIn("more lines", text)
        self.assertLess(text.count("\n"), drift.DIFF_LINE_LIMIT + 40)


if __name__ == "__main__":
    unittest.main()
