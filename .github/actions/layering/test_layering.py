#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Each of layering.py's five rules, and its configuration checks, on a small tree laid out as a library would be."""

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import layering  # noqa: E402

# A layered tree: the shared detail/ at the bottom, a layer of fronts, two families, an extension and an umbrella.
LAYERED = {
    "detail/core.hpp": [],
    "detail/kernel.hpp": ["detail/core.hpp"],
    "traits/is_small.hpp": ["detail/core.hpp"],
    "traits.hpp": ["traits/is_small.hpp"],
    "small_set/small_set.hpp": ["detail/kernel.hpp", "small_set/detail/node.hpp"],
    "small_set/detail/node.hpp": ["detail/core.hpp"],
    "small_map/small_map.hpp": ["detail/kernel.hpp"],
    "ext/boost.hpp": ["small_set/small_set.hpp", "small_map/small_map.hpp"],
}


class Layering(unittest.TestCase):
    def check(self, tree, families=("small_set", "small_map"), extra=""):
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = base / "include" / "xstd" / "demo"
            for header, includes in tree.items():
                path = root / header
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("".join(f"#include <xstd/demo/{i}> // what it needs\n" for i in includes), encoding="utf-8")
            config = base / "layering.toml"
            config.write_text(
                f'root = "{root.as_posix()}"\nshared = "detail"\nlayers = ["traits"]\n'
                f"families = {list(families)!r}\nextensions = [\"ext\"]\n{extra}",
                encoding="utf-8",
            )
            errors, summary = layering.check(config)
            return [error.replace(f"{root.as_posix()}/", "") for error in errors], summary

    def test_layered_tree_passes(self):
        errors, summary = self.check(LAYERED)
        self.assertEqual(errors, [])
        self.assertIn("8 headers", summary)

    def test_shared_detail_includes_no_public_header(self):
        errors, _ = self.check(LAYERED | {"detail/kernel.hpp": ["traits/is_small.hpp"]})
        self.assertIn("detail/kernel.hpp: detail/ includes the public <xstd/demo/traits/is_small.hpp>", errors)

    def test_folders_form_no_cycle(self):
        errors, _ = self.check(LAYERED | {"traits/is_small.hpp": ["small_set/detail/node.hpp"], "small_set/detail/node.hpp": ["traits.hpp"]})
        self.assertTrue(any("the folders small_set, traits reach one another" in error for error in errors), errors)

    def test_no_family_includes_another(self):
        errors, _ = self.check(LAYERED | {"small_map/small_map.hpp": ["small_set/small_set.hpp"]})
        self.assertIn("small_map/small_map.hpp: the small_map family includes the small_set family's <xstd/demo/small_set/small_set.hpp>", errors)

    def test_public_header_is_a_leaf(self):
        errors, _ = self.check(LAYERED | {"small_map/small_map.hpp": ["traits/is_small.hpp"]})
        self.assertIn("small_map/small_map.hpp: includes the public <xstd/demo/traits/is_small.hpp>, which only umbrellas and extensions include", errors)

    def test_folder_detail_is_private(self):
        errors, _ = self.check(LAYERED | {"small_map/small_map.hpp": ["small_set/detail/node.hpp"]})
        self.assertIn("small_map/small_map.hpp: includes the small_set folder's own <xstd/demo/small_set/detail/node.hpp>", errors)

    def test_umbrellas_and_extensions_are_exempt(self):
        errors, _ = self.check(LAYERED | {"small_set.hpp": ["small_set/small_set.hpp", "small_set/detail/node.hpp"], "ext/boost.hpp": ["small_set/small_set.hpp", "traits/is_small.hpp"]})
        self.assertEqual(errors, [])

    def test_unclassified_folder(self):
        errors, _ = self.check(LAYERED | {"misc/thing.hpp": ["detail/core.hpp"]})
        self.assertTrue(any(error.startswith("misc: an unclassified folder") for error in errors), errors)

    def test_families_and_extensions_are_optional(self):
        tree = {"detail/core.hpp": [], "traits/is_small.hpp": ["detail/core.hpp"], "traits.hpp": ["traits/is_small.hpp"]}
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            root = base / "include" / "xstd" / "demo"
            for header, includes in tree.items():
                (root / header).parent.mkdir(parents=True, exist_ok=True)
                (root / header).write_text("".join(f"#include <xstd/demo/{i}>\n" for i in includes), encoding="utf-8")
            config = base / "layering.toml"
            config.write_text(f'root = "{root.as_posix()}"\nshared = "detail"\nlayers = ["traits"]\n', encoding="utf-8")
            errors, _ = layering.check(config)
            self.assertEqual(errors, [])

    def test_root_outside_include_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / "layering.toml"
            config.write_text(f'root = "{directory}/src"\nshared = "detail"\nlayers = []\n', encoding="utf-8")
            with self.assertRaises(SystemExit):
                layering.check(config)


if __name__ == "__main__":
    unittest.main()
