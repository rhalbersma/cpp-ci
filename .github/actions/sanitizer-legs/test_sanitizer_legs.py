#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""The sanitizer legs, sanitizer-legs.sh, crossed with rungs shaped as the toolchain action emits them.

Each leg has to land on its own compiler family's rungs and on no other, carry both its own keys and its rung's, and
the libc++ leg has to drop out, rather than fail, when no libc++ rung is named.
"""

import json
import os
import pathlib
import subprocess
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "sanitizer-legs.sh"
LEGS = json.loads((HERE / "legs.json").read_text())

GCC = {"include": [{"label": "15", "version": "15", "trunk": "false"}, {"label": "16", "version": "16", "trunk": "false"}]}
CLANG = {"include": [{"label": "22", "version": "22", "gcc_version": "15"}]}
LIBCXX = {"include": [{"label": "22 libc++", "version": "22", "gcc_version": "15"}]}


def cross(gcc, clang, libcxx):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_OUTPUT", "GITHUB_ACTIONS")}
    env.update(GCC_RUNGS=json.dumps(gcc), CLANG_RUNGS=json.dumps(clang), LIBCXX_RUNGS="" if libcxx is None else json.dumps(libcxx))
    out = subprocess.run(["bash", str(SCRIPT)], env=env, check=True, capture_output=True, text=True).stdout
    return json.loads(out)["include"]


class SanitizerLegs(unittest.TestCase):
    def test_the_five_legs(self):
        self.assertEqual([leg["id"] for leg in LEGS], ["asan", "asan-libcxx", "ubsan-gcc", "ubsan-clang", "implicit-conversion"])

    def test_each_leg_crosses_with_its_family_only(self):
        entries = cross(GCC, CLANG, LIBCXX)
        pairs = [(e["id"], e["label"]) for e in entries]
        self.assertEqual(pairs, [("asan", "15"), ("asan", "16"), ("asan-libcxx", "22 libc++"),
                                 ("ubsan-gcc", "15"), ("ubsan-gcc", "16"), ("ubsan-clang", "22"),
                                 ("implicit-conversion", "22")])

    def test_an_entry_carries_the_legs_keys_and_the_rungs(self):
        entry = next(e for e in cross(GCC, CLANG, LIBCXX) if e["id"] == "implicit-conversion")
        self.assertEqual(entry["flags"], "-fsanitize=implicit-conversion -fno-sanitize-recover=implicit-conversion")
        self.assertTrue(entry["ignore_foreign_code"])
        self.assertEqual(entry["gcc_version"], "15")

    def test_no_libcxx_rungs_drops_the_libcxx_leg(self):
        ids = {e["id"] for e in cross(GCC, CLANG, None)}
        self.assertNotIn("asan-libcxx", ids)
        self.assertEqual(len(ids), 4)

    def test_no_flag_carries_a_debug_level(self):
        for leg in LEGS:
            self.assertNotIn("-g", leg["flags"].split(), leg["id"])


if __name__ == "__main__":
    unittest.main()
