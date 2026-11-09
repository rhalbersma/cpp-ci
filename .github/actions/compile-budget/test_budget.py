#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""The compile budget's comparison and trace reading on data held in memory,
and, where CXX names a Clang, its measurement of a small header tree."""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import budget  # noqa: E402


def row(lines, classes, functions, seconds=1.0):
    return {"lines": lines, "classes": classes, "functions": functions, "seconds": seconds}


class Trace(unittest.TestCase):
    def test_counts_and_frontend_time_come_from_the_totals(self):
        trace = {"traceEvents": [
            {"name": "Total InstantiateClass", "dur": 900, "args": {"count": 1282}},
            {"name": "Total InstantiateFunction", "dur": 1500, "args": {"count": 630}},
            {"name": "Total Frontend", "dur": 3_250_000, "args": {"count": 2}},
            {"name": "InstantiateClass", "dur": 50, "args": {"detail": "std::vector<int>"}},
        ]}
        counts, seconds = budget.trace_counts(trace)
        self.assertEqual(counts, {"classes": 1282, "functions": 630})
        self.assertAlmostEqual(seconds, 3.25)

    def test_a_header_instantiating_nothing_counts_zero(self):
        counts, _ = budget.trace_counts({"traceEvents": [{"name": "Total Frontend", "dur": 1, "args": {}}]})
        self.assertEqual(counts, {"classes": 0, "functions": 0})


class Compare(unittest.TestCase):
    def test_unchanged_counts_pass_whatever_the_time(self):
        text, over = budget.compare({"a.hpp": row(10, 2, 3, 1.0)}, {"a.hpp": row(10, 2, 3, 9.0)}, 5, 1)
        self.assertEqual(over, [])
        self.assertIn("No header's counts changed, across 1 header.", text)

    def test_growth_above_the_threshold_fails(self):
        text, over = budget.compare({"a.hpp": row(100, 20, 30)}, {"a.hpp": row(100, 22, 30)}, 5, 1)
        self.assertEqual(over, ["a.hpp: classes 20 → 22 (+10.0%)"])
        self.assertIn("20 → 22 (+10.0%) ⚠", text)

    def test_growth_within_the_threshold_is_reported_and_passes(self):
        text, over = budget.compare({"a.hpp": row(1000, 20, 30)}, {"a.hpp": row(1013, 20, 30)}, 5, 1)
        self.assertEqual(over, [])
        self.assertIn("1,000 → 1,013 (+1.3%)", text)

    def test_growth_below_the_minimum_delta_passes(self):
        _, over = budget.compare({"a.hpp": row(10, 2, 3)}, {"a.hpp": row(10, 3, 3)}, 5, 2)
        self.assertEqual(over, [])

    def test_shrinking_passes(self):
        _, over = budget.compare({"a.hpp": row(100, 20, 30)}, {"a.hpp": row(50, 10, 15)}, 5, 1)
        self.assertEqual(over, [])

    def test_growth_from_zero_fails(self):
        text, over = budget.compare({"a.hpp": row(100, 0, 0)}, {"a.hpp": row(100, 0, 4)}, 5, 1)
        self.assertEqual(over, ["a.hpp: functions 0 → 4 (from none)"])
        self.assertIn("0 → 4 (from none) ⚠", text)

    def test_a_new_header_is_listed_and_passes(self):
        text, over = budget.compare({}, {"b.hpp": row(5, 1, 1)}, 5, 1)
        self.assertEqual(over, [])
        self.assertIn("| `b.hpp` | new | 5 | 1 | 1 |", text)

    def test_a_removed_header_is_listed_and_passes(self):
        text, over = budget.compare({"a.hpp": row(5, 1, 1)}, {}, 5, 1)
        self.assertEqual(over, [])
        self.assertIn("| `a.hpp` | removed |", text)

    def test_a_header_that_stops_compiling_fails(self):
        _, over = budget.compare({"a.hpp": row(5, 1, 1)}, {"a.hpp": {"error": "boom"}}, 5, 1)
        self.assertEqual(over, ["a.hpp: does not compile"])


class Report(unittest.TestCase):
    def test_the_costliest_header_leads_and_a_broken_one_trails(self):
        text = budget.report({"a.hpp": row(10, 1, 1), "b.hpp": row(900, 9, 9), "c.hpp": {"error": "boom"}})
        rows = [line for line in text.splitlines() if line.startswith("| `")]
        self.assertEqual([r.split("`")[1] for r in rows], ["b.hpp", "a.hpp", "c.hpp"])
        self.assertIn("does not compile: boom", rows[-1])


class Headers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for name in ("lib.hpp", "lib/a.hpp", "lib/detail/b.hpp", "lib/sub/detail/c.hpp", "lib/sub/d.hpp"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text("#pragma once\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_header_the_glob_matches_is_taken_without_exclusions(self):
        self.assertEqual(budget.headers(self.root, "**/*.hpp"),
                         ["lib.hpp", "lib/a.hpp", "lib/detail/b.hpp", "lib/sub/d.hpp", "lib/sub/detail/c.hpp"])

    def test_an_exclusion_drops_every_detail_directory_at_any_depth(self):
        self.assertEqual(budget.headers(self.root, "**/*.hpp", ["*/detail/*"]), ["lib.hpp", "lib/a.hpp", "lib/sub/d.hpp"])

    def test_exclusions_add_up(self):
        self.assertEqual(budget.headers(self.root, "**/*.hpp", ["*/detail/*", "lib/sub/*"]), ["lib.hpp", "lib/a.hpp"])

    def test_excluding_everything_is_refused(self):
        with self.assertRaises(SystemExit):
            budget.measure(self.root, "**/*.hpp", "clang++", [], 1, ["*"])


@unittest.skipUnless(shutil.which(os.environ.get("CXX", "")) and "clang" in os.environ.get("CXX", ""), "CXX names no Clang")
class Measure(unittest.TestCase):
    def test_a_header_tree_is_measured_and_its_counts_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "include"
            (root / "lib" / "detail").mkdir(parents=True)
            (root / "lib" / "detail" / "box.hpp").write_text("#pragma once\ntemplate<class T> struct box { T t; auto get() const -> T { return t; } };\n")
            (root / "lib" / "use.hpp").write_text("#pragma once\n#include <lib/detail/box.hpp>\ninline auto f() -> int { return box<int>{1}.get() + box<long>{2}.get(); }\n")
            (root / "lib" / "broken.hpp").write_text("#pragma once\nstruct x { y z; };\n")
            first = budget.measure(root, "lib/*.hpp", os.environ["CXX"], ["-std=c++20"], 2)
            second = budget.measure(root, "lib/*.hpp", os.environ["CXX"], ["-std=c++20"], 1)
        self.assertEqual(sorted(first), ["lib/broken.hpp", "lib/use.hpp"])
        self.assertIn("error", first["lib/broken.hpp"])
        use = first["lib/use.hpp"]
        self.assertEqual(use["classes"], 2)
        self.assertGreaterEqual(use["functions"], 2)
        self.assertGreater(use["lines"], 2)
        self.assertEqual({k: use[k] for k in budget.COUNTS}, {k: second["lib/use.hpp"][k] for k in budget.COUNTS})
        json.dumps(first)


if __name__ == "__main__":
    unittest.main()
