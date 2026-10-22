#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Unit tests for librarian.py's dependency reading and package selection."""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from librarian import closure, first_alternatives  # noqa: E402

# The shape of the PPA for an install of g++-15 on noble, cut down: gcc-15's
# packages need gcc-16's runtime libraries, and libc6 comes from the archive.
PPA = {
    "g++-15": ("gcc-15", ["gcc-15-base", "gcc-15", "libstdc++-15-dev", "libc6"]),
    "gcc-15": ("gcc-15", ["gcc-15-base", "cpp-15", "libgcc-15-dev", "binutils"]),
    "cpp-15": ("gcc-15", ["gcc-15-base"]),
    "gcc-15-base": ("gcc-15", []),
    "libgcc-15-dev": ("gcc-15", ["gcc-15-base", "libgcc-s1", "libasan8"]),
    "libstdc++-15-dev": ("gcc-15", ["gcc-15-base", "libgcc-15-dev", "libstdc++6"]),
    "libstdc++6": ("gcc-16", ["gcc-16-base", "libc6", "libgcc-s1"]),
    "libgcc-s1": ("gcc-16", ["gcc-16-base", "libc6"]),
    "libasan8": ("gcc-16", ["gcc-16-base", "libc6", "libgcc-s1"]),
    "gcc-16-base": ("gcc-16", []),
    "libgfortran5": ("gcc-16", ["gcc-16-base", "libgcc-s1", "libquadmath0"]),
    "libquadmath0": ("gcc-16", ["gcc-16-base", "libc6"]),
    "gcc-13": ("gcc-13", ["gcc-13-base", "cpp-13"]),
}


class Stub:
    def __init__(self) -> None:
        self.downloaded: list[str] = []

    def publication(self, name: str) -> dict[str, str] | None:
        if name not in PPA:
            return None
        return {"name": name, "version": "1", "source": PPA[name][0], "self_link": name}

    def download(self, published: dict[str, str]) -> dict[str, object]:
        self.downloaded.append(published["name"])
        return {**published, "depends": PPA[published["name"]][1]}


class FirstAlternatives(unittest.TestCase):
    def test_versions_and_qualifiers_are_dropped(self) -> None:
        field = "gcc-15-base (= 15.2.0-14ubuntu1~24~ppa1), libc6 (>= 2.34), python3:any"
        self.assertEqual(first_alternatives(field), ["gcc-15-base", "libc6", "python3"])

    def test_only_the_first_alternative_counts(self) -> None:
        self.assertEqual(first_alternatives("libgcc-s1 (>= 3.0) | libgcc1, cpp-15"), ["libgcc-s1", "cpp-15"])

    def test_architecture_restrictions_are_dropped(self) -> None:
        self.assertEqual(first_alternatives("libc6-dev [amd64], libx32gcc-s1 <!nocheck>"), ["libc6-dev", "libx32gcc-s1"])

    def test_an_empty_field_names_nothing(self) -> None:
        self.assertEqual(first_alternatives(""), [])
        self.assertEqual(first_alternatives(" , "), [])


class Closure(unittest.TestCase):
    def test_takes_what_the_ppa_publishes_and_leaves_the_archive_alone(self) -> None:
        stub = Stub()
        taken = closure(["g++-15"], [], stub.publication, stub.download)
        self.assertEqual(
            set(taken),
            {"g++-15", "gcc-15", "cpp-15", "gcc-15-base", "libgcc-15-dev", "libstdc++-15-dev",
             "libstdc++6", "libgcc-s1", "libasan8", "gcc-16-base"},
        )
        self.assertNotIn("libc6", taken)
        self.assertNotIn("binutils", taken)

    def test_downloads_each_package_once(self) -> None:
        stub = Stub()
        closure(["g++-15"], [], stub.publication, stub.download)
        self.assertEqual(len(stub.downloaded), len(set(stub.downloaded)))

    def test_upgrades_installed_runtime_libraries_of_a_source_it_reached(self) -> None:
        stub = Stub()
        taken = closure(["g++-15"], ["libgfortran5", "gcc-13", "libfoo"], stub.publication, stub.download)
        self.assertIn("libgfortran5", taken)
        self.assertIn("libquadmath0", taken)

    def test_leaves_the_runners_own_compilers_alone(self) -> None:
        stub = Stub()
        taken = closure(["g++-15"], ["gcc-13"], stub.publication, stub.download)
        self.assertNotIn("gcc-13", taken)
        self.assertNotIn("gcc-13", stub.downloaded)

    def test_an_unpublished_request_is_an_error(self) -> None:
        stub = Stub()
        with self.assertRaises(LookupError):
            closure(["g++-99"], [], stub.publication, stub.download)
        self.assertEqual(stub.downloaded, [])


if __name__ == "__main__":
    unittest.main()
