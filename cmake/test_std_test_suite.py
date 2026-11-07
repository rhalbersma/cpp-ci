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

    def test_verify_under_clang(self):
        # nodiscard_extensions.verify.cpp and its kind: Clang checks every expected-warning, GCC cannot.
        body = "// UNSUPPORTED: c++03\n// ADDITIONAL_COMPILE_FLAGS: -Wno-deprecated\n"
        kind, _, options, xfail = self.classify("a.verify.cpp", body, CLANG)
        self.assertEqual((kind, xfail), ("compile", ""))
        self.assertEqual(options, [*sts.LIBCXX_VERIFY, "-Wno-deprecated"])
        self.assertEqual(self.classify("a.verify.cpp", body)[:2], ("skip", "a test of Clang's diagnostics, which only Clang verifies"))

    def test_compile_fail(self):
        # compare_copy_constructible.compile.fail.cpp: the suite's own expected compile failure, on any compiler.
        self.assertEqual(self.classify("a.compile.fail.cpp", "")[::3], ("compile", ".compile.fail.cpp"))
        self.assertEqual(self.classify("a.compile.fail.cpp", "// XFAIL: gcc\n")[3], "XFAIL: gcc")

    def test_unsupported_dialects(self):
        body = "// UNSUPPORTED: c++03, c++11, c++14, c++17, c++20\n"
        self.assertEqual(self.classify("a.pass.cpp", body)[0], "run")
        body = "// UNSUPPORTED: c++03, c++11, c++14, c++17, c++20, c++23\n"
        self.assertEqual(self.classify("a.pass.cpp", body)[:2], ("skip", "UNSUPPORTED: c++23"))

    def test_requires(self):
        self.assertEqual(self.classify("a.pass.cpp", "// REQUIRES: std-at-least-c++26\n")[0], "skip")
        self.assertEqual(self.classify("a.pass.cpp", "// REQUIRES: std-at-least-c++17\n")[0], "run")
        self.assertEqual(self.classify("a.pass.cpp", "// REQUIRES: std-at-least-c++26\n", CLANG)[0], "run")
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

    def test_xfail_run_if(self):
        body = '// { dg-do run { target c++20 } }\n// { dg-xfail-run-if "assert should fail" { *-*-* } }\n'
        self.assertNotEqual(self.classify(body)[3], "")
        body = '// { dg-do run }\n// { dg-xfail-run-if "" { *-*-* } { "-D_GLIBCXX_DEBUG" } }\n'
        self.assertEqual(self.classify(body)[3], "")
        body = '// { dg-do run }\n// { dg-xfail-run-if "" { powerpc64-*-freebsd* } }\n'
        self.assertEqual(self.classify(body)[3], "")
        body = '// { dg-do compile }\n// { dg-xfail-run-if "" { *-*-* } }\n'
        self.assertEqual(self.classify(body)[3], "")

    def test_requirements(self):
        self.assertEqual(self.classify("// { dg-require-effective-target exceptions_enabled }\n")[0], "run")
        self.assertEqual(self.classify("// { dg-require-normal-mode \"\" }\n")[0], "skip")
        self.assertEqual(self.classify('// { dg-error "no match" }\n')[0], "skip")

    # operators/1_neg.cc, cut down: two dg-error lines on the comparisons they reject.
    NEG = '// { dg-do compile }\n#include <set>\nvoid f() {\n  a != b; // { dg-error "no match" }\n  a == b; // { dg-error "no match" }\n}\n'

    def test_diagnose(self):
        self.assertEqual(self.classify(self.NEG)[0], "diagnose")
        self.assertEqual(sts.libstdcxx_diagnostics(self.NEG, GCC), [{"line": 4, "pattern": "no match"}, {"line": 5, "pattern": "no match"}])
        clang = sts.Config("clang", "22.1.8", "libstdc++", 23, "x86_64-pc-linux-gnu")
        self.assertEqual(self.classify(self.NEG, clang)[:2], ("skip", "a test of GCC's diagnostics, which only GCC reports"))
        self.assertEqual(self.classify('// { dg-excess-errors "" }\n')[0], "skip")

    def test_diagnostic_lines(self):
        body = '\n'.join([
            "// { dg-do compile }",
            'x; // { dg-error "here" }',
            '// { dg-error "two" "" { target *-*-* } .-1 }',
            '// { dg-error "in a header" "" { target *-*-* } 0 }',
            '// { dg-error "absolute" "" { target *-*-* } 9 }',
            '// { dg-error "elsewhere" "" { target c++17_down } }',
            '// { dg-error "known" "" { xfail *-*-* } }',
        ])
        self.assertEqual(sts.libstdcxx_diagnostics(body, GCC), [
            {"line": 2, "pattern": "here"}, {"line": 2, "pattern": "two"}, {"line": 0, "pattern": "in a header"}, {"line": 9, "pattern": "absolute"},
        ])
        # Only directives whose selectors drop them in this dialect: the test compiles cleanly, so it is a compile.
        self.assertEqual(self.classify('// { dg-do compile }\n// { dg-error "x" "" { target c++17_down } }\n')[0], "compile")

    def test_check_diagnostics(self):
        with tempfile.TemporaryDirectory() as root:
            source = pathlib.Path(root) / "1_neg.cc"
            source.write_text("")
            output = "\n".join([
                f"{source}: In function 'void f()':",
                f"{source}:4:7: error: no match for 'operator!=' (operand types are 'it' and 'it')",
                f"{root}/include/set.hpp:12:3: note: candidate: 'operator=='",
            ])
            expected = sts.libstdcxx_diagnostics(self.NEG, GCC)
            self.assertEqual(sts.check_diagnostics(output, str(source), expected), ["no diagnostic at line 5 matches 'no match'"])
            self.assertEqual(sts.check_diagnostics(output, str(source), [{"line": 0, "pattern": "candidate"}]), [])
            self.assertEqual(sts.check_diagnostics(output, str(source), [{"line": 12, "pattern": "candidate"}]), ["no diagnostic at line 12 matches 'candidate'"])
            # A pattern Python cannot read is matched as text.
            self.assertEqual(sts.check_diagnostics(output, str(source), [{"line": 4, "pattern": "operator!=' (operand"}]), [])

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

    def test_force_include(self):
        # P0088R3_variant: a copy of libc++'s tests, which includes a header from the STL's LLVM submodule.
        with tempfile.TemporaryDirectory() as root:
            kind, reason, _, _ = sts.msvc_classify("tests/P0088R3_variant/test.cpp", "#include <msvc_stdlib_force_include.h> // Must precede\n", GCC, pathlib.Path(root))
            self.assertEqual(kind, "skip")
            self.assertIn("msvc_stdlib_force_include.h", reason)

    def test_death_test(self):
        # GH_005090_stl_hardening: death tests through <crtdbg.h>, which only the MSVC runtime has.
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            (root / "tests" / "GH_2").mkdir(parents=True)
            (root / "tests" / "GH_2" / "env.lst").write_text('PM_CL="/EHsc /std:c++latest"\n')
            text = '#include <test_death.hpp>\n'
            kind, reason, _, _ = sts.msvc_classify("tests/GH_2/test.cpp", text, GCC, root)
            self.assertEqual(kind, "skip")
            self.assertIn("crtdbg.h", reason)
            self.assertEqual(sts.msvc_classify("tests/GH_2/test.cpp", text, MSVC, root)[0], "run")

    def test_stacktrace_link(self):
        # P0881R7_stacktrace: libstdc++ before 16 keeps std::stacktrace in libstdc++exp.
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            (root / "tests" / "P0881R7_stacktrace").mkdir(parents=True)
            (root / "tests" / "P0881R7_stacktrace" / "env.lst").write_text('PM_CL="/EHsc /std:c++latest"\n')
            config = sts.Config("gcc", "15.2.0", "libstdc++", 23, "x86_64-linux-gnu", stacktrace_link=("-lstdc++exp",))
            self.assertEqual(sts.msvc_classify("tests/P0881R7_stacktrace/test.cpp", "#include <stacktrace>\n", config, root)[2], ["-lstdc++exp"])
            self.assertEqual(sts.msvc_classify("tests/P0881R7_stacktrace/test.cpp", "#include <vector>\n", config, root)[2], [])
            self.assertEqual(sts.msvc_classify("tests/P0881R7_stacktrace/test.cpp", "#include <stacktrace>\n", GCC, root)[2], [])


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

    def test_stale_hash_lenient(self):
        # Against a trunk, an exception whose line still reads applies, and its drift is still reported.
        mapping = self.mapping(assertion=[{"file": "f.cpp", "sha256": "0" * 64, "line": "int main() {", "drop": True, "departure": "x"}])
        errors = []
        text, applied, _ = sts.rewrite(self.TEXT, "f.cpp", sts.sha256(self.TEXT.encode()), mapping, errors, lenient=True)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(applied), 1)
        self.assertIn("std-test-suite: x", text)

    def test_when(self):
        # P0912R5_coroutine asserts noexcept(done()), an MSVC STL extension libc++ lacks: the exception applies there alone.
        digest = sts.sha256(self.TEXT.encode())
        mapping = self.mapping(assertion=[{"file": "f.cpp", "sha256": digest, "line": "int main() {", "to": "int main() { // x", "departure": "d", "when": "stdlib=libc++"}])
        errors = []
        self.assertIn("std-test-suite: d", sts.rewrite(self.TEXT, "f.cpp", digest, mapping, errors, config=CLANG)[0])
        self.assertNotIn("std-test-suite: d", sts.rewrite(self.TEXT, "f.cpp", digest, mapping, errors, config=GCC)[0])
        self.assertEqual(errors, [])
        # An exception that does not apply is still held to the file.
        stale = self.mapping(assertion=[{**mapping.assertion[0], "line": "gone();"}])
        sts.rewrite(self.TEXT, "f.cpp", digest, stale, errors, config=GCC)
        self.assertEqual(errors, ["f.cpp: no line reads 'gone();'"])

    def test_load_checks_when(self):
        self.assertEqual(self.load('[[libcxx.expect]]\nfile = "f"\nsha256 = "0"\nfails = "compile"\nparagraph = "p"\nwhen = "stdlib=libc++"\n').expect[0]["when"], "stdlib=libc++")
        with self.assertRaises(SystemExit):
            self.load('[[libcxx.expect]]\nfile = "f"\nsha256 = "0"\nfails = "compile"\nparagraph = "p"\nwhen = "stdlib=libc++ &&"\n')

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


class Registration(unittest.TestCase):
    def test_every_test_skipped(self):
        # libstdc++'s suite under libc++ skips every test, so nothing is written before tests.cmake.
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            tree, out = root / "tree", root / "build" / "libstdcxx"
            (tree / "libstdc++-v3/testsuite/23_containers/vector/bool").mkdir(parents=True)
            (tree / "libstdc++-v3/testsuite/23_containers/vector/bool/1.cc").write_text("int main() {}\n")
            mapping = root / "m.toml"
            mapping.write_text('[libstdcxx]\ntake = [{ path = "libstdc++-v3/testsuite/23_containers/vector/bool" }]\n')
            argv = ["--suite", "libstdcxx", "--mapping", str(mapping), "--tree", str(tree), "--binary-dir", str(out), "--tag", "releases/gcc-15.2.0", "--offline"]
            argv += ["--compiler", "clang", "--version", "22.1.8", "--stdlib", "libc++", "--std", "26", "--target", "x86_64-pc-linux-gnu"]
            self.assertEqual(sts.main(argv), 0)
            self.assertNotIn("std_test_suite_add(", (out / "tests.cmake").read_text())
            self.assertIn('"kind": "skip"', (out / "manifest.json").read_text())

    def test_a_diagnostics_test(self):
        # 23781_neg.cc: the rewritten copy must fail with the errors its dg-error lines name, unless the mapping says otherwise.
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            tree, out = root / "tree", root / "build" / "libstdcxx"
            (tree / "libstdc++-v3/testsuite/23_containers/set").mkdir(parents=True)
            test = tree / "libstdc++-v3/testsuite/23_containers/set/23781_neg.cc"
            test.write_text('// { dg-do compile }\n#include <set>\nstd::set<int>::iterator it = NULL; // { dg-error "conversion" }\n')
            mapping = root / "m.toml"
            mapping.write_text('[libstdcxx]\ntake = [{ path = "libstdc++-v3/testsuite/23_containers/set" }]\nrewrite = [{ from = "std::set<int>", to = "xstd::bit_set" }]\n')
            argv = ["--suite", "libstdcxx", "--mapping", str(mapping), "--tree", str(tree), "--binary-dir", str(out), "--tag", "releases/gcc-15.2.0", "--offline"]
            argv += ["--compiler", "gcc", "--version", "15.2.0", "--stdlib", "libstdc++", "--std", "23", "--target", "x86_64-linux-gnu"]
            self.assertEqual(sts.main(argv), 0)
            self.assertIn("KIND diagnose", (out / "tests.cmake").read_text())
            self.assertIn("DIAGNOSTICS", (out / "tests.cmake").read_text())
            written = (out / "diagnostics" / "libstdcxx.23_containers.set.23781_neg.json").read_text()
            self.assertIn('"line": 3', written)
            digest = sts.sha256(test.read_bytes())
            mapping.write_text(mapping.read_text() + f'[[libstdcxx.expect]]\nfile = "libstdc++-v3/testsuite/23_containers/set/23781_neg.cc"\nsha256 = "{digest}"\nfails = "compile"\nparagraph = "not with these errors"\n')
            self.assertEqual(sts.main(argv), 0)
            self.assertIn("KIND compile", (out / "tests.cmake").read_text())
            self.assertIn("EXPECT compile", (out / "tests.cmake").read_text())

    def support_tree(self, root, helper, mapping_text):
        # libc++'s charconv test calls std::to_chars only from its support header, never from the test.
        tree, out = root / "tree", root / "build" / "libcxx"
        (tree / "libcxx/test/std/utilities/charconv").mkdir(parents=True)
        (tree / "libcxx/test/support").mkdir(parents=True)
        (tree / "libcxx/test/std/utilities/charconv/integral.pass.cpp").write_text('#include "charconv_test_helpers.h"\nint main() { test(); }\n')
        (tree / "libcxx/test/support/charconv_test_helpers.h").write_text(helper)
        mapping = root / "m.toml"
        mapping.write_text(mapping_text)
        argv = ["--suite", "libcxx", "--mapping", str(mapping), "--tree", str(tree), "--binary-dir", str(out), "--tag", "llvmorg-22.1.8", "--offline"]
        argv += ["--compiler", "clang", "--version", "22.1.8", "--stdlib", "libc++", "--std", "26", "--target", "x86_64-pc-linux-gnu"]
        return tree, out, argv

    TAKE = '[libcxx]\ntake = [{ path = "libcxx/test/std/utilities/charconv" }]\nrewrite = [{ from = "std::to_chars(", to = "xstd::to_chars(" }]\n'
    HELPER = "#pragma once\ninline void test() { std::to_chars(buf, buf, 1); }\n"

    def test_support_header_without_mapping(self):
        # Without the header in the mapping, nothing reaches the test, which then tests the standard library.
        with tempfile.TemporaryDirectory() as root:
            _, out, argv = self.support_tree(pathlib.Path(root), self.HELPER, self.TAKE)
            self.assertEqual(sts.main(argv), 0)
            self.assertNotIn("std_test_suite_add(", (out / "tests.cmake").read_text())

    def test_support_header_rewritten(self):
        with tempfile.TemporaryDirectory() as root:
            tree, out, argv = self.support_tree(pathlib.Path(root), self.HELPER, self.TAKE + 'support = [{ path = "libcxx/test/support/charconv_test_helpers.h" }]\n')
            self.assertEqual(sts.main(argv), 0)
            self.assertIn("xstd::to_chars(buf, buf, 1);", (out / "support/charconv_test_helpers.h").read_text())
            cmake = (out / "tests.cmake").read_text()
            self.assertIn("std_test_suite_add(libcxx.utilities.charconv.integral ", cmake)
            # The copy comes before the original, so the test's include finds it first.
            self.assertIn(f'set(STD_TEST_SUITE_INCLUDE_DIRS_libcxx "{(out / "support").as_posix()}" "{(tree / "libcxx/test/support").as_posix()}")', cmake)
            self.assertIn('"name": "charconv_test_helpers.h"', (out / "manifest.json").read_text())

    def test_a_named_run(self):
        # A second subject's run of the same suite names its targets, include directories and manifest apart.
        with tempfile.TemporaryDirectory() as root:
            tree, out, argv = self.support_tree(pathlib.Path(root), self.HELPER, self.TAKE + 'support = [{ path = "libcxx/test/support/charconv_test_helpers.h" }]\n')
            self.assertEqual(sts.main(argv + ["--name", "libcxx-fixed-set"]), 0)
            cmake = (out / "tests.cmake").read_text()
            self.assertIn("std_test_suite_add(libcxx-fixed-set.utilities.charconv.integral SUITE libcxx-fixed-set ", cmake)
            self.assertIn("set(STD_TEST_SUITE_INCLUDE_DIRS_libcxx_fixed_set ", cmake)
            manifest = (out / "manifest.json").read_text()
            self.assertIn('"name": "libcxx-fixed-set"', manifest)
            self.assertIn('"suite": "libcxx"', manifest)

    def test_warn_stale(self):
        # A trunk run: a stale expected failure fails configuration, unless --warn-stale reports it, applies it and runs.
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            tree, out = root / "tree", root / "build" / "libstdcxx"
            (tree / "libstdc++-v3/testsuite/23_containers/set").mkdir(parents=True)
            (tree / "libstdc++-v3/testsuite/23_containers/set/merge.cc").write_text("std::set<int> s;\nint main() { s.merge(s); }\n")
            mapping = root / "m.toml"
            mapping.write_text('[libstdcxx]\ntake = [{ path = "libstdc++-v3/testsuite/23_containers/set" }]\nrewrite = [{ from = "std::set<int>", to = "xstd::bit_set" }]\n'
                               + f'[[libstdcxx.expect]]\nfile = "libstdc++-v3/testsuite/23_containers/set/merge.cc"\nsha256 = "{"0" * 64}"\nfails = "compile"\nparagraph = "no nodes"\n')
            argv = ["--suite", "libstdcxx", "--mapping", str(mapping), "--tree", str(tree), "--binary-dir", str(out), "--tag", "a-trunk-commit", "--offline"]
            argv += ["--compiler", "gcc", "--version", "17.0.0", "--stdlib", "libstdc++", "--std", "23", "--target", "x86_64-linux-gnu"]
            self.assertEqual(sts.main(argv), 1)
            self.assertEqual(sts.main(argv + ["--warn-stale"]), 0)
            self.assertIn("EXPECT compile", (out / "tests.cmake").read_text())
            self.assertIn("its expected failure was written against sha256", (out / "manifest.json").read_text())

    def libstdcxx_run(self, root, files, mapping_text, compiler="gcc", stdlib="libstdc++"):
        tree, out = root / "tree", root / "build" / "libstdcxx"
        for path, text in files.items():
            (tree / path).parent.mkdir(parents=True, exist_ok=True)
            (tree / path).write_text(text)
        mapping = root / "m.toml"
        mapping.write_text(mapping_text)
        argv = ["--suite", "libstdcxx", "--mapping", str(mapping), "--tree", str(tree), "--binary-dir", str(out), "--tag", "releases/gcc-15.2.0", "--offline"]
        argv += ["--compiler", compiler, "--version", "15.2.0", "--stdlib", stdlib, "--std", "23", "--target", "x86_64-linux-gnu"]
        return out, sts.main(argv)

    HASH = "libstdc++-v3/testsuite/20_util/hash"
    TAKE_HASH = f'[libstdcxx]\ntake = [{{ path = "{HASH}" }}]\nrewrite = [{{ from = "std::hash<", to = "xstd::hash<" }}]\n'

    def test_support_header_beside_tests(self):
        # chi2_q_*.cc include "chi2_quality.h" from their own directory, and call std::hash only there.
        files = {f"{self.HASH}/chi2_quality.h": "inline auto h = std::hash<int>();\n", f"{self.HASH}/chi2_q_uniform_random.cc": '#include "chi2_quality.h"\nint main() {}\n'}
        with tempfile.TemporaryDirectory() as root:
            out, status = self.libstdcxx_run(pathlib.Path(root), files, self.TAKE_HASH)
            self.assertEqual(status, 0)
            self.assertNotIn("std_test_suite_add(", (out / "tests.cmake").read_text())
        with tempfile.TemporaryDirectory() as root:
            out, status = self.libstdcxx_run(pathlib.Path(root), files, self.TAKE_HASH + f'support = [{{ path = "{self.HASH}/chi2_quality.h" }}]\n')
            self.assertEqual(status, 0)
            self.assertIn("xstd::hash<int>()", (out / "tests" / self.HASH / "chi2_quality.h").read_text())
            cmake = (out / "tests.cmake").read_text()
            self.assertIn("std_test_suite_add(libstdcxx.20_util.hash.chi2_q_uniform_random ", cmake)
            self.assertNotIn(str(out / "support"), cmake)

    def test_link_options(self):
        # 19_diagnostics/stacktrace: dg-options "-lstdc++exp" names a library, which only a link reads.
        files = {f"{self.HASH}/stacktrace.cc": '// { dg-options "-lstdc++exp" }\nauto h = std::hash<int>();\nint main() {}\n'}
        with tempfile.TemporaryDirectory() as root:
            out, status = self.libstdcxx_run(pathlib.Path(root), files, self.TAKE_HASH)
            self.assertEqual(status, 0)
            cmake = (out / "tests.cmake").read_text()
            self.assertIn(' LINK_OPTIONS "-lstdc++exp")', cmake)
            self.assertNotIn(' OPTIONS "-lstdc++exp"', cmake)

    def test_additional_files(self):
        # chi2_q_document_words.cc opens a text DejaGnu copies from testsuite/data into where it runs.
        files = {f"{self.HASH}/words.cc": '// { dg-additional-files "words.txt" }\nauto h = std::hash<int>();\nint main() {}\n', "libstdc++-v3/testsuite/data/words.txt": "a b c\n"}
        with tempfile.TemporaryDirectory() as root:
            out, status = self.libstdcxx_run(pathlib.Path(root), files, self.TAKE_HASH)
            self.assertEqual(status, 0)
            where = out / "data" / "libstdcxx.20_util.hash.words"
            self.assertEqual((where / "words.txt").read_text(), "a b c\n")
            self.assertIn(f' WORKING_DIRECTORY "{where.as_posix()}")', (out / "tests.cmake").read_text())
        files.pop("libstdc++-v3/testsuite/data/words.txt")
        with tempfile.TemporaryDirectory() as root:
            out, status = self.libstdcxx_run(pathlib.Path(root), files, self.TAKE_HASH)
            self.assertEqual(status, 0)
            self.assertIn("dg-additional-files names words.txt", (out / "manifest.json").read_text())

    def expect(self, path, text, extra=""):
        return f'[[libstdcxx.expect]]\nfile = "{path}"\nsha256 = "{sts.sha256(text.encode())}"\nfails = "compile"\nparagraph = "p"\n{extra}'

    def test_expect_when(self):
        # An expected failure that holds on one standard library alone applies there, and is held to its file everywhere.
        text = "auto h = std::hash<int>();\nint main() {}\n"
        files = {f"{self.HASH}/abi.cc": text}
        mapping = self.TAKE_HASH + self.expect(f"{self.HASH}/abi.cc", text, 'when = "gcc"\n')
        with tempfile.TemporaryDirectory() as root:
            out, status = self.libstdcxx_run(pathlib.Path(root), files, mapping)
            self.assertEqual(status, 0)
            self.assertIn("EXPECT compile", (out / "tests.cmake").read_text())
        with tempfile.TemporaryDirectory() as root:
            out, status = self.libstdcxx_run(pathlib.Path(root), files, mapping, compiler="clang")
            self.assertEqual(status, 0)
            self.assertNotIn("EXPECT", (out / "tests.cmake").read_text())
        with tempfile.TemporaryDirectory() as root:
            _, status = self.libstdcxx_run(pathlib.Path(root), files, mapping.replace(sts.sha256(text.encode()), "0" * 64), compiler="clang")
            self.assertEqual(status, 1)

    def test_expected_failure_on_a_skipped_suite(self):
        # libstdc++'s suite under libc++ skips every test, so an expected failure there is not stale; one no build runs is.
        text = "auto h = std::hash<int>();\nint main() {}\n"
        never = '// { dg-do run { target *-*-plan9* } }\n' + text
        with tempfile.TemporaryDirectory() as root:
            _, status = self.libstdcxx_run(pathlib.Path(root), {f"{self.HASH}/abi.cc": text}, self.TAKE_HASH + self.expect(f"{self.HASH}/abi.cc", text), compiler="clang", stdlib="libc++")
            self.assertEqual(status, 0)
        with tempfile.TemporaryDirectory() as root:
            _, status = self.libstdcxx_run(pathlib.Path(root), {f"{self.HASH}/abi.cc": never}, self.TAKE_HASH + self.expect(f"{self.HASH}/abi.cc", never), compiler="clang", stdlib="libc++")
            self.assertEqual(status, 1)

    def test_a_name_is_lower_case_words(self):
        with tempfile.TemporaryDirectory() as root:
            _, _, argv = self.support_tree(pathlib.Path(root), self.HELPER, self.TAKE)
            with self.assertRaises(SystemExit):
                sts.main(argv + ["--name", "libcxx.fixed set"])

    def test_support_header_exception(self):
        # A per-line exception may name a support header as well as a test.
        digest = sts.sha256(self.HELPER.encode())
        exception = f'[[libcxx.assertion]]\nfile = "libcxx/test/support/charconv_test_helpers.h"\nsha256 = "{digest}"\nline = "#pragma once"\nto = "#pragma once // guarded"\ndeparture = "d"\n'
        with tempfile.TemporaryDirectory() as root:
            _, out, argv = self.support_tree(pathlib.Path(root), self.HELPER, self.TAKE + 'support = [{ path = "libcxx/test/support/charconv_test_helpers.h" }]\n' + exception)
            self.assertEqual(sts.main(argv), 0)
            self.assertIn("// std-test-suite: d", (out / "support/charconv_test_helpers.h").read_text())

    def test_support_header_stale(self):
        # A header no rewrite changes, one that is not there, and one outside the support headers all fail.
        for support in ("libcxx/test/support/test_macros.h", "libcxx/test/support/missing.h", "libcxx/test/std/utilities/charconv/integral.pass.cpp"):
            with tempfile.TemporaryDirectory() as root:
                tree, _, argv = self.support_tree(pathlib.Path(root), self.HELPER, self.TAKE + f'support = [{{ path = "{support}" }}]\n')
                (tree / "libcxx/test/support/test_macros.h").write_text("#pragma once\n")
                self.assertEqual(sts.main(argv), 1, support)


if __name__ == "__main__":
    unittest.main()
