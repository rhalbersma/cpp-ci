#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""The coverage gate, gcovr.sh, run on a small tree laid out as a library would be.

Each case builds the fixture with --coverage, runs its test, then runs the gate
from the tree's root as the Coverage workflow does. What a threshold that never
fires would hide is the point: an uncovered line or branch has to fail, an
excluded path has to stay out, and the reports have to land where the
workflow's later steps look for them.

    CXX and GCOV name a matching compiler and gcov, g++ and gcov by default.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
GATE = HERE / "gcovr.sh"
CXX = os.environ.get("CXX", "g++")
GCOV = os.environ.get("GCOV", "gcov")

# The library's header: a branch, an assert and a project assert macro whose false arms no passing test takes, an
# allocation whose throw branch only a failure would take, a returned local whose closing brace holds only the unwind
# that a throw would run, and a template whose two instantiations take one branch each.
LIBRARY = """\
#pragma once
#include <cassert>
#include <string>
#include <vector>

#define LIB_ASSERT(c) assert(c)

inline auto sign(int x) -> int
{
        assert(x != 1000);
        LIB_ASSERT(x != 999);
        if (x < 0) {
                return -1;
        }
        return 1;
}

inline auto width(int x) -> int
{
        auto const s = std::to_string(x);
        auto const v = std::vector<int>(3);
        return static_cast<int>(s.size() + v.size());
}

inline auto suffixed(std::string const& a) -> std::string
{
        auto s = std::string("x");
        s += a;
        return s;
}

template<class T>
auto clamp_low(T x) -> T
{
        if (x > T(0)) {
                return x;
        }
        return T(0);
}

struct point
{
        int x = 0;
        point();
        point(point const&);
};
"""

# The library's one source: special members defaulted out of line, the copy never called.
POINT = """\
#include "lib.hpp"

point::point() = default;
point::point(point const&) = default;
"""

FULL = """\
#include "../src/lib.hpp"

auto main() -> int
{
        point p;
        // Exits 0 whichever calls a case drops: coverage is under test, not the results.
        [[maybe_unused]] volatile auto const sum = sign(-1) + sign(1) + width(p.x) + static_cast<int>(suffixed("y").size()) + clamp_low(1) + static_cast<int>(clamp_low(-1L));
}
"""


class Gate(unittest.TestCase):
    def run_gate(self, files, **env):
        """Build files[test/main.cpp] and any vendor/ source with --coverage, run it, and run the gate; return its result and the root."""
        root = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory()))
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        # A file the glob "test/.*" would match, as a .clang-tidy beside the tests does.
        (root / "test" / ".clang-tidy").write_text("Checks: '-*'\n")
        (root / "build").mkdir()
        # The report directory is the caller's, as the workflow's build tree is.
        (root / env.get("BUILD_DIR", "build")).mkdir(exist_ok=True)
        sources = sorted(str(p.relative_to(root)) for p in root.rglob("*.cpp"))
        subprocess.run([CXX, "-std=c++17", "--coverage", "-O0", *sources, "-o", "build/main"], cwd=root, check=True)
        subprocess.run(["build/main"], cwd=root, check=True)
        result = subprocess.run(["bash", str(GATE)], cwd=root, capture_output=True, text=True, env={**os.environ, "GCOV": GCOV, **env})
        return result, root

    def report(self, root):
        return (root / "build" / "coverage.txt").read_text()

    def test_full_coverage_passes_and_writes_both_reports(self):
        result, root = self.run_gate({"src/lib.hpp": LIBRARY, "src/point.cpp": POINT, "test/main.cpp": FULL})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The workflow's summary, Codecov upload and artifact read these two paths.
        self.assertTrue((root / "build" / "coverage.txt").is_file())
        self.assertTrue((root / "build" / "coverage.xml").is_file())
        self.assertIn("src/lib.hpp", self.report(root))

    def test_the_default_excludes_keep_the_tests_out_as_regexes(self):
        # Globbed rather than read as a regex, "test/.*" would match only test/.clang-tidy and let test/main.cpp in.
        _, root = self.run_gate({"src/lib.hpp": LIBRARY, "src/point.cpp": POINT, "test/main.cpp": FULL})
        self.assertNotIn("test/main.cpp", self.report(root))

    def test_an_uncovered_line_fails_on_the_line_threshold_alone(self):
        # An inline function no test calls emits no code for gcov to see, so the uncovered lines are a source file's.
        twice = "auto twice(int x) -> int\n{\n        return 2 * x;\n}\n"
        files = {"src/lib.hpp": LIBRARY, "src/point.cpp": POINT, "src/twice.cpp": twice, "test/main.cpp": FULL}
        result, _ = self.run_gate(files)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        result, _ = self.run_gate(files, FAIL_UNDER_LINE="0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_an_uncovered_branch_fails_on_the_branch_threshold_alone(self):
        # Every line runs, but x < 0 is only ever false.
        library = LIBRARY.replace("        if (x < 0) {\n                return -1;\n        }\n        return 1;\n", "        return x < 0 ? -1 : 1;\n")
        files = {"src/lib.hpp": library, "src/point.cpp": POINT, "test/main.cpp": FULL.replace("sign(-1)", "sign(2)")}
        result, _ = self.run_gate(files)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        result, _ = self.run_gate(files, FAIL_UNDER_BRANCH="0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_thresholds_are_the_inputs(self):
        files = {"src/lib.hpp": LIBRARY, "src/point.cpp": POINT, "test/main.cpp": FULL.replace("sign(-1)", "sign(2)")}
        result, _ = self.run_gate(files, FAIL_UNDER_LINE="0", FAIL_UNDER_BRANCH="0")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_an_excluded_path_stays_out(self):
        # A vendored source the tests never call: it fails the gate unless the caller excludes its path.
        vendor = "auto unused(int x) -> int\n{\n        return x + 1;\n}\n"
        files = {"src/lib.hpp": LIBRARY, "src/point.cpp": POINT, "test/main.cpp": FULL, "vendor/extra.cpp": vendor}
        result, _ = self.run_gate(files)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        result, root = self.run_gate(files, EXCLUDE="test/.* build/.* vendor/.*")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("vendor/extra.cpp", self.report(root))

    def test_the_reports_follow_build_dir(self):
        files = {"src/lib.hpp": LIBRARY, "src/point.cpp": POINT, "test/main.cpp": FULL}
        result, root = self.run_gate(files, BUILD_DIR="out", EXCLUDE="test/.* build/.* out/.*")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((root / "out" / "coverage.txt").is_file())
        self.assertTrue((root / "out" / "coverage.xml").is_file())
        self.assertFalse((root / "build" / "coverage.xml").exists())

if __name__ == "__main__":
    if shutil.which("gcovr") is None:
        sys.exit("gcovr is not on PATH; the install-gcovr action puts it there")
    unittest.main()
