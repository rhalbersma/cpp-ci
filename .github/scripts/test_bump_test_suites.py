#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Unit tests for bump_test_suites.py's choice of tag, against tag lists of each upstream's shape."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bump_test_suites import moves, newest  # noqa: E402


class Newest(unittest.TestCase):
    def test_a_later_patch_of_the_same_major(self):
        tags = ["releases/gcc-15.1.0", "releases/gcc-15.2.0", "releases/gcc-15.3.0"]
        self.assertEqual(newest("releases/gcc-15.2.0", tags), "releases/gcc-15.3.0")

    def test_never_the_next_major(self):
        # The pattern ls-remote takes, releases/gcc-15*, also matches GCC 150 and beyond.
        tags = ["releases/gcc-15.2.0", "releases/gcc-16.1.0", "releases/gcc-150.1.0"]
        self.assertEqual(newest("releases/gcc-15.2.0", tags), "releases/gcc-15.2.0")

    def test_numbers_compare_as_numbers(self):
        tags = ["llvmorg-22.1.8", "llvmorg-22.1.10", "llvmorg-22.1.9"]
        self.assertEqual(newest("llvmorg-22.1.8", tags), "llvmorg-22.1.10")

    def test_a_release_candidate_is_no_release(self):
        tags = ["llvmorg-22.1.8", "llvmorg-22.1.9-rc1", "llvmorg-22-init"]
        self.assertEqual(newest("llvmorg-22.1.8", tags), "llvmorg-22.1.8")

    def test_the_visual_studio_series(self):
        tags = ["vs-2022-17.9", "vs-2022-17.14", "vs-2022-17.10", "vs-2026-18.0"]
        self.assertEqual(newest("vs-2022-17.9", tags), "vs-2022-17.14")

    def test_never_backwards(self):
        self.assertEqual(newest("vs-2022-17.14", ["vs-2022-17.13"]), "vs-2022-17.14")


class Moves(unittest.TestCase):
    TABLE = {
        "gcc": {
            "stable": {"version": "15", "test_suite": "releases/gcc-15.2.0"},
            "development": {"version": "17", "test_suite": "09561afe3ed2335d3ff6ba7c8ceb927e9223fff5"},
            "qualification": {"version": "16"},
        },
        "clang": {"stable": {"version": "22", "test_suite": "llvmorg-22.1.8"}},
        "mingw": {"stable": {"version": "15", "test_suite": "releases/gcc-15.2.0"}},
    }

    def test_a_pin_moves_and_a_commit_or_a_missing_pin_stays(self):
        upstream = {
            "https://github.com/gcc-mirror/gcc": ["releases/gcc-15.2.0", "releases/gcc-15.3.0"],
            "https://github.com/llvm/llvm-project": ["llvmorg-22.1.8"],
        }
        asked = []

        def tags_of(repository, pin):
            asked.append(pin)
            return upstream[repository]

        self.assertEqual(moves(self.TABLE, tags_of), [("gcc", "stable", "releases/gcc-15.2.0", "releases/gcc-15.3.0")])
        # The development commit is never looked up, and mingw, which no suite reads, is left alone.
        self.assertEqual(asked, ["releases/gcc-15.2.0", "llvmorg-22.1.8"])


if __name__ == "__main__":
    unittest.main()
