#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""The marker readers and the mapping checks of std_test_suite.py, on lines the suites actually carry."""

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import std_test_suite as sts  # noqa: E402

GCC = sts.Config("gcc", "15.2.0", "libstdc++", 23, "x86_64-linux-gnu")
CLANG = sts.Config("clang", "22.1.8", "libc++", 26, "x86_64-pc-linux-gnu")
MSVC = sts.Config("msvc", "19.44.35211", "msvc", 23, "x64-pc-windows-msvc")


class LitExpressions(unittest.TestCase):
    def test_features(self):
        features = sts.lit_features(GCC)
        self.assertTrue(sts.lit_eval("c++23", features))
        self.assertTrue(sts.lit_eval("gcc-15 && !clang", features))
        self.assertTrue(sts.lit_eval("(clang || gcc) && stdlib=libstdc++", features))
        self.assertFalse(sts.lit_eval("no-exceptions", features))
        self.assertFalse(sts.lit_eval("gcc-14", features))

    def test_regex_identifiers(self):
        self.assertTrue(sts.lit_eval("target={{.+}}-linux-gnu", sts.lit_features(GCC)))
        self.assertFalse(sts.lit_eval("target={{.+}}-windows{{.*}}", sts.lit_features(GCC)))
        self.assertTrue(sts.lit_eval("clang-{{2[0-9]}}", sts.lit_features(CLANG)))

    def test_malformed(self):
        with self.assertRaises(ValueError):
            sts.lit_eval("(gcc", set())
        with self.assertRaises(ValueError):
            sts.lit_eval("gcc &&", set())


class Libcxx(unittest.TestCase):
    def classify(self, name, body, config=GCC):
        return sts.libcxx_classify(f"libcxx/test/std/x/{name}", body, config)

    def test_kinds(self):
        self.assertEqual(self.classify("a.pass.cpp", "")[0], "run")
        self.assertEqual(self.classify("a.compile.pass.cpp", "")[0], "compile")
        self.assertEqual(self.classify("a.link.pass.cpp", "")[0], "build")
        self.assertEqual(self.classify("a.verify.cpp", "")[0], "skip")
        self.assertEqual(self.classify("a.fmt", "")[0], "skip")

    def test_unsupported_dialects(self):
        body = "// UNSUPPORTED: c++03, c++11, c++14, c++17, c++20\n"
        self.assertEqual(self.classify("a.pass.cpp", body)[0], "run")
        body = "// UNSUPPORTED: c++03, c++11, c++14, c++17, c++20, c++23\n"
        self.assertEqual(self.classify("a.pass.cpp", body)[:2], ("skip", "UNSUPPORTED: c++23"))

    def test_requires(self):
        self.assertEqual(self.classify("a.pass.cpp", "// REQUIRES: std-at-least-c++26\n")[0], "skip")
        self.assertEqual(self.classify("a.pass.cpp", "// REQUIRES: c++23 || c++26\n")[0], "run")

    def test_conditional_flags(self):
        body = "// ADDITIONAL_COMPILE_FLAGS(has-fconstexpr-steps): -fconstexpr-steps=2000000\n"
        self.assertEqual(self.classify("a.pass.cpp", body)[2], [])
        self.assertEqual(self.classify("a.pass.cpp", body, CLANG)[2], ["-fconstexpr-steps=2000000"])
        body = "// ADDITIONAL_COMPILE_FLAGS: %{std}\n"
        self.assertEqual(self.classify("a.pass.cpp", body)[0], "skip")

    def test_xfail(self):
        self.assertEqual(self.classify("a.pass.cpp", "// XFAIL: gcc\n")[3], "XFAIL: gcc")
        self.assertEqual(self.classify("a.pass.cpp", "// XFAIL: availability-fp_to_chars-missing\n")[3], "")

    def test_custom_run(self):
        self.assertEqual(self.classify("a.pass.cpp", "// RUN: %{build}\n")[0], "skip")


class Libstdcxx(unittest.TestCase):
    def classify(self, body, config=GCC):
        return sts.libstdcxx_classify("libstdc++-v3/testsuite/x/1.cc", body, config)

    def test_default_is_run(self):
        self.assertEqual(self.classify("int main() {}\n")[0], "run")

    def test_dialect_targets(self):
        self.assertEqual(self.classify("// { dg-do run { target c++11 } }\n")[0], "run")
        self.assertEqual(self.classify("// { dg-do compile { target c++20 } }\n")[0], "compile")
        self.assertEqual(self.classify("// { dg-do run { target c++26 } }\n")[0], "skip")
        self.assertEqual(self.classify("// { dg-do run { target c++17_only } }\n")[0], "skip")
        self.assertEqual(self.classify("// { dg-do run { target { ! c++20 } } }\n")[0], "skip")

    def test_options(self):
        body = '// { dg-options "-std=gnu++20 -O2 -DX=1" }\n// { dg-do run { target c++20 } }\n'
        self.assertEqual(self.classify(body)[2], ["-O2", "-DX=1"])

    def test_skip_if(self):
        body = '// { dg-skip-if "" { *-*-* } { "-D_GLIBCXX_DEBUG" } }\n'
        self.assertEqual(self.classify(body)[0], "run")
        body = '// { dg-options "-D_GLIBCXX_DEBUG" }\n// { dg-skip-if "" { *-*-* } { "-D_GLIBCXX_DEBUG" } }\n'
        self.assertEqual(self.classify(body)[0], "skip")
        body = '// { dg-skip-if "" { powerpc64-*-freebsd* } }\n'
        self.assertEqual(self.classify(body)[0], "run")

    def test_xfail_triple(self):
        self.assertEqual(self.classify("// { dg-do run { xfail *-*-darwin8.[0-4].* } }\n")[3], "")
        self.assertNotEqual(self.classify("// { dg-do run { xfail *-*-* } }\n")[3], "")

    def test_requirements(self):
        self.assertEqual(self.classify("// { dg-require-effective-target exceptions_enabled }\n")[0], "run")
        self.assertEqual(self.classify("// { dg-require-normal-mode \"\" }\n")[0], "skip")
        self.assertEqual(self.classify('// { dg-error "no match" }\n')[0], "skip")

    def test_other_standard_libraries(self):
        self.assertEqual(self.classify("", MSVC)[0], "skip")
        self.assertEqual(self.classify("", CLANG)[0], "skip")
        self.assertEqual(self.classify("", sts.Config("clang", "22.1.8", "libstdc++", 23, "x86_64-pc-linux-gnu"))[0], "run")


class Msvc(unittest.TestCase):
    def test_env(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            (root / "tests" / "GH_1").mkdir(parents=True)
            (root / "usual_matrix.lst").write_text(
                "RUNALL_CROSSLIST\n"
                'PM_CL="/EHsc /MD /D_ITERATOR_DEBUG_LEVEL=0 /std:c++20"\n'
                'PM_CL="/EHsc /MD /D_ITERATOR_DEBUG_LEVEL=0 /std:c++latest /permissive-"\n'
                'ASAN PM_CL="-fsanitize=address /Zi" PM_LINK="/debug"\n'
                'PM_COMPILER="clang-cl" PM_CL="-fno-ms-compatibility /std:c++latest"\n'
            )
            (root / "tests" / "GH_1" / "env.lst").write_text("RUNALL_INCLUDE ..\\..\\usual_matrix.lst\n")
            (root / "tests" / "GH_1" / "test.cpp").write_text("int main() {}\n")
            kind, _, options, _ = sts.msvc_classify("tests/GH_1/test.cpp", "", MSVC, root)
            self.assertEqual(kind, "run")
            self.assertEqual(options, ["/EHsc", "/D_ITERATOR_DEBUG_LEVEL=0", "/permissive-"])
            _, _, options, _ = sts.msvc_classify("tests/GH_1/test.cpp", "", GCC, root)
            self.assertEqual(options, ["-D_ITERATOR_DEBUG_LEVEL=0"])


class Mapping(unittest.TestCase):
    TEXT = "#include <vector>\nint main() {\n  static_assert(std::is_same_v<C::pointer, bool*>);\n  std::vector<bool> v;\n}\n"

    def mapping(self, **kwargs):
        return sts.Mapping(**kwargs)

    def test_rewrite_and_exception(self):
        digest = sts.sha256(self.TEXT.encode())
        mapping = self.mapping(
            include=["xstd/bits/bit_vector.hpp"],
            rewrite=[{"regex": r"std::vector<bool>", "to": "xstd::bit_vector"}],
            assertion=[{"file": "f.cpp", "sha256": digest, "line": "static_assert(std::is_same_v<C::pointer, bool*>);", "drop": True, "departure": "a proxy pointer"}],
        )
        errors = []
        text, applied, touched = sts.rewrite(self.TEXT, "f.cpp", digest, mapping, errors)
        self.assertEqual(errors, [])
        self.assertTrue(text.startswith("#include <xstd/bits/bit_vector.hpp>\n#line 1\n#include <vector>\n"))
        self.assertIn("  // std-test-suite: a proxy pointer\n", text)
        self.assertIn("xstd::bit_vector v;", text)
        self.assertEqual(len(applied), 1)
        self.assertTrue(touched)

    def test_stale_hash(self):
        mapping = self.mapping(assertion=[{"file": "f.cpp", "sha256": "0" * 64, "line": "int main() {", "drop": True, "departure": "x"}])
        errors = []
        sts.rewrite(self.TEXT, "f.cpp", sts.sha256(self.TEXT.encode()), mapping, errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("written against sha256", errors[0])

    def test_vanished_line(self):
        digest = sts.sha256(self.TEXT.encode())
        mapping = self.mapping(assertion=[{"file": "f.cpp", "sha256": digest, "line": "assert(v.data());", "drop": True, "departure": "no data()"}])
        errors = []
        sts.rewrite(self.TEXT, "f.cpp", digest, mapping, errors)
        self.assertEqual(errors, ["f.cpp: no line reads 'assert(v.data());'"])

    def load(self, text):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "m.toml"
            path.write_text(text)
            return sts.load_mapping([path], "libcxx")

    def test_load_merges_common_and_suite_tables(self):
        mapping = self.load('include = ["a.hpp"]\n[libcxx]\ninclude = ["b.hpp"]\n[libstdcxx]\ninclude = ["c.hpp"]\n')
        self.assertEqual(mapping.include, ["a.hpp", "b.hpp"])

    def test_load_rejects_misspelt_keys(self):
        for text in (
            'tak = [{ path = "x" }]\n',
            '[libcxx]\nrewrites = [{ from = "a", to = "b" }]\n',
            '[libcxx]\ntake = [{ path = "x", selct = "y" }]\n',
            '[[libcxx.expect]]\nfile = "f"\nsha256 = "0"\nfail = "run"\nparagraph = "[a]/1"\n',
            '[[libcxx.assertion]]\nfile = "f"\nsha256 = "0"\nline = "x"\ndrop = true\ndeparture = "d"\nreason = "r"\n',
            '[libcxx]\nrewrite = [{ from = "a", regex = "b", to = "c" }]\n',
            '[libcxx]\ninclude = [{ header = "a.hpp" }]\n',
        ):
            with self.subTest(text=text), self.assertRaises(SystemExit):
                self.load(text)

    def test_load_rejects_unnamed_departures(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "m.toml"
            path.write_text('[[libcxx.expect]]\nfile = "f.cpp"\nsha256 = "0"\nfails = "compile"\nparagraph = ""\n')
            with self.assertRaises(SystemExit):
                sts.load_mapping([path], "libcxx")
            path.write_text('[[libcxx.assertion]]\nfile = "f.cpp"\nsha256 = "0"\nline = "x"\ndrop = true\ndeparture = ""\n')
            with self.assertRaises(SystemExit):
                sts.load_mapping([path], "libcxx")


if __name__ == "__main__":
    unittest.main()
