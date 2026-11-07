#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""ci_local.py's legs, from fixture stubs, without a network or a compiler.

The stubs are written into a temporary caller; the shared workflows' defaults, the sanitizer-legs script and the
scripts the legs run are this checkout's own. The toolchain table is a fixed copy, so the legs' names and command
lines stay put when the real rungs move.
"""

import contextlib
import io
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ci_local  # noqa: E402

PIN = "0123456789abcdef0123456789abcdef01234567"
TABLE = {
    "gcc": {
        "stable": {"label": "15", "version": "15", "trunk": "false"},
        "qualification": {"label": "16", "version": "16", "trunk": "false"},
        "development": {"label": "17-SVN", "version": "17", "trunk": "true"},
    },
    "clang": {
        "stable": {"label": "22", "version": "22", "llvm_suffix": "-22", "gcc_version": "15", "gcc_trunk": "false", "cxxflags": ""},
        "qualification": {"label": "23", "version": "23", "llvm_suffix": "-23", "gcc_version": "16", "gcc_trunk": "false", "cxxflags": ""},
        "development": {"label": "24-SVN", "version": "24", "llvm_suffix": "", "gcc_version": "17", "gcc_trunk": "true",
                        "cxxflags": "--gcc-toolchain=/opt/gcc-latest"},
    },
}


def stub(jobs):
    """A workflow file whose jobs call cpp-ci workflows: {job id: (workflow, with:)}."""
    lines = ["name: Stub", "on: push", "jobs:"]
    for job_id, (workflow, given) in jobs.items():
        lines += [f"  {job_id}:", f"    uses: rhalbersma/cpp-ci/.github/workflows/{workflow}.yml@{PIN}"]
        if given:
            lines.append("    with:")
            lines += [f"      {key}: {value}" for key, value in given.items()]
    return "\n".join(lines) + "\n"


# A caller shaped as xstd-ints is: the ladders and their extra legs, the checks, the Windows and macOS stubs, and a
# job of its own that calls no cpp-ci workflow.
WORKFLOWS = {
    "actionlint.yml": stub({"actionlint": ("actionlint", {})}),
    "clang-format.yml": stub({"clang_format": ("clang-format", {"paths": "include test examples"})}),
    "gcc.yml": stub({
        "gcc_ladder": ("gcc", {"vcpkg_features": "test"}),
        "i686": ("gcc", {"arch": "x86", "tiers": "stable", "build_types": "Debug", "vcpkg_features": "test"}),
        "gcc_bmi2": ("gcc", {"tiers": "stable", "build_types": "Release", "cxx_flags": "-march=x86-64-v3"}),
    }),
    "clang.yml": stub({
        "clang_ladder": ("clang", {"vcpkg_features": "test"}),
        "clang_bmi2": ("clang", {"tiers": "stable", "build_types": "Release", "cxx_flags": "-march=x86-64-v3"}),
    }),
    "clang-libc++.yml": stub({"clang_libcxx_ladder": ("clang-libc++", {"vcpkg_features": "test"})}),
    "clang-tidy.yml": stub({"clang_tidy_ladder": ("clang-tidy", {
        "self_sufficiency_regex": "'${{ github.workspace }}/build/test/header_self_sufficiency/.*'",
        "include_exclude": "'include/xstd/config/*'",
        "adapted_dirs": "build/_deps/a-src vcpkg_installed/x64-linux/include build/vcpkg_installed/x64-linux/include",
        "vcpkg_features": "test",
    })}),
    "coverage.yml": stub({"coverage": ("coverage", {"exclude": "'test/.* build/.* examples/.*'", "vcpkg_features": "test"})}),
    "sanitizers.yml": stub({"sanitizers_ladder": ("sanitizers", {"vcpkg_features": "test"})}),
    "consumption.yml": stub({"consumption": ("consumption", {})}),
    "msvc.yml": stub({"msvc_ladder": ("visual-studio", {}), "msvc_x86": ("visual-studio", {"arch": "x86"})}),
    "apple-clang.yml": stub({"apple_clang_ladder": ("apple-clang", {})}),
    "pages.yml": "name: Pages\non: push\njobs:\n  site:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: mkdocs build\n",
    "changes-user.yml": "name: Changes\non: push\njobs:\n  changes:\n    uses: ./.github/workflows/changes.yml\n",
}


class Fixture(unittest.TestCase):
    """A temporary caller with WORKFLOWS, a vcpkg manifest and two consumption models."""

    workflows = WORKFLOWS
    manifest = True
    deps = "vcpkg"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.caller = pathlib.Path(directory.name)
        (self.caller / ".github/workflows").mkdir(parents=True)
        for name, text in self.workflows.items():
            (self.caller / ".github/workflows" / name).write_text(text)
        if self.manifest:
            (self.caller / "vcpkg.json").write_text('{"name": "demo"}\n')
        for model in ("find_package", "fetch_content"):
            (self.caller / "test/consumer" / model).mkdir(parents=True)
            (self.caller / "test/consumer" / model / "CMakeLists.txt").write_text("project(demo)\n")
        self.out = self.caller / "build/ci-local"
        self.ctx = ci_local.Context(self.caller, self.out, vcpkg_root=pathlib.Path("/vcpkg"), jobs=4, table=TABLE,
                                    deps=self.deps, boost_prefix=pathlib.Path("/usr/local"))
        self.legs = ci_local.discover(self.ctx)

    def leg(self, name):
        return next(leg for leg in self.legs if leg.name == name)

    def configure(self, name):
        return next(step for step in self.leg(name).steps if step.argv[:2] == ["cmake", "-S"])

    def runnable(self):
        return [leg.name for leg in self.legs if not leg.skip]

    def skipped(self):
        return {leg.name: leg.skip for leg in self.legs if leg.skip}


class Discovery(Fixture):
    def test_legs_in_run_order(self):
        self.assertEqual(self.runnable(), [
            "actionlint", "format",
            "gcc-15", "i686-15", "gcc-bmi2-15", "clang-22", "clang-bmi2-22", "clang-libcxx-22",
            "gcc-16", "clang-23", "gcc-17-svn", "clang-24-svn",
            "clang-tidy-22", "clang-tidy-23", "clang-tidy-24-svn",
            "coverage",
            "asan-15", "asan-libcxx-22", "ubsan-gcc-15", "ubsan-clang-22", "implicit-conversion-22",
            "consumption",
        ])

    def test_windows_and_macos_stubs_are_skipped_with_a_reason(self):
        skipped = self.skipped()
        self.assertIn("Windows", skipped["msvc"])
        self.assertIn("Windows", skipped["msvc-x86"])
        self.assertIn("macOS", skipped["apple-clang"])

    def test_a_callers_own_jobs_are_listed_nowhere(self):
        names = {leg.name for leg in self.legs}
        self.assertNotIn("site", names)
        self.assertNotIn("changes", names)

    def test_every_name_is_unique(self):
        names = [leg.name for leg in self.legs]
        self.assertEqual(len(names), len(set(names)))

    def test_listing_names_the_pin_and_each_skip(self):
        text = ci_local.listing(self.ctx, self.legs)
        self.assertIn(f"pinning cpp-ci {PIN[:7]}", text)
        self.assertRegex(text, r"\n  apple-clang +apple-clang\.yml/apple_clang_ladder: runs on macOS")


class Inputs(Fixture):
    def test_defaults_come_from_the_shared_workflow(self):
        given = ci_local.inputs(self.ctx, "gcc", {})
        self.assertEqual(given["tiers"], "stable,qualification,development")
        self.assertEqual(given["build_types"], "Release,Debug")
        self.assertEqual(given["debug_flags"], "-g1")
        self.assertEqual(given["vcpkg_triplet"], "x64-linux-gcc")

    def test_with_overrides_the_default(self):
        given = ci_local.inputs(self.ctx, "gcc", {"tiers": "stable", "debug_flags": ""})
        self.assertEqual(given["tiers"], "stable")
        self.assertEqual(given["debug_flags"], "")

    def test_github_workspace_reads_as_the_callers_root(self):
        given = ci_local.inputs(self.ctx, "clang-tidy", {"self_sufficiency_regex": "${{ github.workspace }}/build/x/.*"})
        self.assertEqual(given["self_sufficiency_regex"], f"{self.caller}/build/x/.*")

    def test_an_empty_debug_flags_passes_no_debug_flags(self):
        given = ci_local.inputs(self.ctx, "gcc", {"debug_flags": ""})
        self.assertNotIn("-DCMAKE_CXX_FLAGS_DEBUG", " ".join(ci_local.tail_args(given)))


class Selection(Fixture):
    def test_debug_where_listed_else_the_first(self):
        self.assertEqual(ci_local.build_type("Release,Debug"), "Debug")
        self.assertEqual(ci_local.build_type("Release"), "Release")
        self.assertEqual(ci_local.build_type("RelWithDebInfo, Release"), "RelWithDebInfo")
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", self.configure("gcc-bmi2-15").argv)
        self.assertIn("-DCMAKE_BUILD_TYPE=Debug", self.configure("gcc-16").argv)

    def test_tiers_name_the_rungs(self):
        self.assertEqual([tier for tier, _ in ci_local.rungs(self.ctx, "gcc", "qualification, development")],
                         ["qualification", "development"])
        self.assertEqual(ci_local.rungs(self.ctx, "gcc", "nonesuch"), [])

    def test_libcxx_runs_at_the_stable_rung_alone(self):
        skipped = self.skipped()
        self.assertTrue(self.leg("clang-libcxx-22").steps)
        for name in ("clang-libcxx-23", "clang-libcxx-24-svn"):
            self.assertIn("libc++", skipped[name])
            self.assertEqual(self.leg(name).steps, [])

    def test_stable_keeps_the_stable_and_tier_free_legs(self):
        names = [leg.name for leg in ci_local.select(self.legs, [], stable=True)]
        self.assertIn("actionlint", names)
        self.assertIn("gcc-15", names)
        self.assertIn("coverage", names)
        self.assertNotIn("gcc-16", names)
        self.assertNotIn("clang-tidy-23", names)

    def test_globs_select_in_the_order_given(self):
        names = [leg.name for leg in ci_local.select(self.legs, ["clang-tidy-2[23]", "gcc-1*", "format"], stable=False)]
        self.assertEqual(names, ["clang-tidy-22", "clang-tidy-23", "gcc-15", "gcc-16", "gcc-17-svn", "format"])

    def test_a_glob_can_select_a_skipped_leg(self):
        chosen = ci_local.select(self.legs, ["msvc*"], stable=False)
        self.assertEqual([leg.name for leg in chosen], ["msvc", "msvc-x86"])
        self.assertTrue(all(leg.skip for leg in chosen))

    def test_a_pattern_matching_nothing_is_an_error(self):
        with self.assertRaises(SystemExit):
            ci_local.select(self.legs, ["gcc-99"], stable=False)


class Sanitizers(Fixture):
    def test_each_leg_crosses_with_the_stable_rung(self):
        for name in ("asan-15", "asan-libcxx-22", "ubsan-gcc-15", "ubsan-clang-22", "implicit-conversion-22"):
            self.assertEqual(self.leg(name).tier, "stable")
            self.assertTrue(self.leg(name).steps)

    def test_the_other_rungs_are_ci_s(self):
        skipped = self.skipped()
        for name in ("asan-16", "asan-17-svn", "asan-libcxx-23", "ubsan-clang-24-svn", "implicit-conversion-23"):
            self.assertIn("stable rung", skipped[name])

    def test_flags_reach_the_link_and_options_the_test(self):
        argv = self.configure("ubsan-gcc-15").argv
        self.assertIn("-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=undefined -fno-sanitize-recover=undefined ", argv)
        self.assertIn("-DVCPKG_TARGET_TRIPLET=x64-linux-gcc", argv)
        test = self.leg("ubsan-gcc-15").steps[-1]
        self.assertEqual(test.argv[0], "ctest")
        self.assertEqual(test.env["UBSAN_OPTIONS"], "print_stacktrace=1")

    def test_implicit_conversion_ignores_foreign_code(self):
        leg = self.leg("implicit-conversion-22")
        ignorelist = self.out / "implicit-conversion-22/sanitizer-ignorelist.txt"
        self.assertEqual(leg.steps[0].argv[4], str(ignorelist))
        self.assertIn("src:*/vcpkg_installed/*", leg.steps[0].argv)
        flags = next(arg for arg in self.configure("implicit-conversion-22").argv if arg.startswith("-DCMAKE_CXX_FLAGS="))
        self.assertIn(f"-fsanitize-ignorelist={ignorelist}", flags)

    def test_the_libcxx_leg_drops_the_libstdcxx_pairing(self):
        step = self.configure("asan-libcxx-22")
        self.assertIn("-DVCPKG_TARGET_TRIPLET=x64-linux-libcxx", step.argv)
        self.assertNotIn("--gcc-install-dir", " ".join(step.argv))
        self.assertNotIn("VCPKG_CLANG_FLAGS", step.env)


class Pairing(Fixture):
    def test_a_clang_rung_without_cxxflags_names_its_libstdcxx(self):
        step = self.configure("clang-23")
        self.assertIn("-DCMAKE_CXX_FLAGS=--gcc-install-dir=/usr/lib/gcc/x86_64-linux-gnu/16 ", step.argv)
        self.assertEqual(step.env["VCPKG_CLANG_FLAGS"], "--gcc-install-dir=/usr/lib/gcc/x86_64-linux-gnu/16")

    def test_a_clang_rung_with_cxxflags_keeps_them(self):
        step = self.configure("clang-24-svn")
        self.assertIn("-DCMAKE_CXX_FLAGS=--gcc-toolchain=/opt/gcc-latest ", step.argv)
        self.assertEqual(step.env["VCPKG_CLANG_FLAGS"], "--gcc-toolchain=/opt/gcc-latest")

    def test_clang_tidy_pairs_with_the_gcc_tier(self):
        self.assertIn("-DCMAKE_CXX_FLAGS=--gcc-install-dir=/usr/lib/gcc/x86_64-linux-gnu/15", self.configure("clang-tidy-24-svn").argv)

    def test_the_sanitizer_clang_legs_pair_compile_and_link(self):
        argv = self.configure("ubsan-clang-22").argv
        pairing = "--gcc-install-dir=/usr/lib/gcc/x86_64-linux-gnu/15"
        self.assertTrue(any(arg.startswith("-DCMAKE_CXX_FLAGS=") and pairing in arg for arg in argv))
        self.assertTrue(any(arg.startswith("-DCMAKE_EXE_LINKER_FLAGS=") and pairing in arg for arg in argv))


class Vcpkg(Fixture):
    def vcpkg(self, name):
        return [arg for arg in self.configure(name).argv if "VCPKG" in arg or "TOOLCHAIN_FILE" in arg]

    def test_gcc(self):
        overlay = f"{ci_local.CPP_CI}/.github/actions/vcpkg-overlay/triplets"
        self.assertEqual(self.vcpkg("gcc-16"), [
            "-DCMAKE_TOOLCHAIN_FILE=/vcpkg/scripts/buildsystems/vcpkg.cmake", "-DVCPKG_TARGET_TRIPLET=x64-linux-gcc",
            "-DVCPKG_MANIFEST_FEATURES=test", f"-DVCPKG_OVERLAY_TRIPLETS={overlay}",
        ])
        self.assertEqual(self.configure("gcc-16").env["VCPKG_GCC_VERSION"], "16")

    def test_x86(self):
        argv = self.configure("i686-15").argv
        self.assertIn("-DVCPKG_TARGET_TRIPLET=x86-linux-gcc", argv)
        self.assertIn("-DCMAKE_C_FLAGS=-m32", argv)
        self.assertIn("-DCMAKE_CXX_FLAGS=-m32 ", argv)

    def test_clang(self):
        self.assertIn("-DVCPKG_TARGET_TRIPLET=x64-linux-clang", self.vcpkg("clang-22"))
        self.assertEqual(self.configure("clang-22").env["VCPKG_CLANG_VERSION"], "22")

    def test_libcxx(self):
        step = self.configure("clang-libcxx-22")
        self.assertIn("-DVCPKG_TARGET_TRIPLET=x64-linux-libcxx", step.argv)
        self.assertIn("-DCMAKE_CXX_FLAGS=-stdlib=libc++ ", step.argv)
        self.assertEqual(step.env, {"VCPKG_BINARY_SOURCES": "clear;default,readwrite", "VCPKG_CLANG_VERSION": "22"})

    def test_no_features_passes_none(self):
        self.assertNotIn("-DVCPKG_MANIFEST_FEATURES=test", self.vcpkg("gcc-bmi2-15"))
        self.assertEqual(ci_local.features_arg(" a, b ,"), ["-DVCPKG_MANIFEST_FEATURES=a;b"])

    def test_clang_tidy_and_coverage_take_the_plain_triplet(self):
        for name in ("clang-tidy-22", "coverage"):
            self.assertIn("-DVCPKG_TARGET_TRIPLET=x64-linux", self.vcpkg(name))
            self.assertFalse(any(arg.startswith("-DVCPKG_OVERLAY_TRIPLETS") for arg in self.vcpkg(name)))


class NoManifest(Fixture):
    manifest = False

    def test_no_manifest_configures_without_vcpkg(self):
        self.assertFalse(any("VCPKG" in arg for arg in self.configure("gcc-15").argv))


class CommandLines(Fixture):
    def test_a_gcc_leg(self):
        build = self.out / "gcc-15"
        leg = self.leg("gcc-15")
        self.assertEqual(leg.steps[0].argv, [
            "cmake", "-S", str(self.caller), "-B", str(build), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Debug",
            "-DCMAKE_C_COMPILER=gcc-15", "-DCMAKE_CXX_COMPILER=g++-15", "-DCMAKE_C_FLAGS=", "-DCMAKE_CXX_FLAGS=",
            "-DCMAKE_TOOLCHAIN_FILE=/vcpkg/scripts/buildsystems/vcpkg.cmake", "-DVCPKG_TARGET_TRIPLET=x64-linux-gcc",
            "-DVCPKG_MANIFEST_FEATURES=test", f"-DVCPKG_OVERLAY_TRIPLETS={ci_local.CPP_CI}/.github/actions/vcpkg-overlay/triplets",
            "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF", "-DCMAKE_CXX_FLAGS_DEBUG=-g1",
        ])
        self.assertEqual(leg.steps[1].argv, ["cmake", "--build", str(build), "-v", "--parallel", "4"])
        self.assertEqual(leg.steps[2].argv, ["ctest", "--test-dir", str(build), "--output-on-failure"])

    def test_a_clang_leg(self):
        build = self.out / "clang-bmi2-22"
        self.assertEqual(self.configure("clang-bmi2-22").argv, [
            "cmake", "-S", str(self.caller), "-B", str(build), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_C_COMPILER=clang-22", "-DCMAKE_CXX_COMPILER=clang++-22",
            "-DCMAKE_CXX_FLAGS=--gcc-install-dir=/usr/lib/gcc/x86_64-linux-gnu/15 -march=x86-64-v3",
            "-DCMAKE_TOOLCHAIN_FILE=/vcpkg/scripts/buildsystems/vcpkg.cmake", "-DVCPKG_TARGET_TRIPLET=x64-linux-clang",
            f"-DVCPKG_OVERLAY_TRIPLETS={ci_local.CPP_CI}/.github/actions/vcpkg-overlay/triplets",
            "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF", "-DCMAKE_CXX_FLAGS_DEBUG=-g1",
        ])

    def test_clang_tidy_rebases_the_build_tree(self):
        build = self.out / "clang-tidy-22"
        step = self.leg("clang-tidy-22").steps[1]
        self.assertEqual(step.argv, ["bash", f"{ci_local.CPP_CI}/.github/actions/clang-tidy/clang-tidy.sh"])
        self.assertEqual(step.cwd, self.caller)
        self.assertEqual(step.env["SELF_SUFFICIENCY_REGEX"], f"{build}/test/header_self_sufficiency/.*")
        self.assertEqual(step.env["ADAPTED_DIRS"], f"{build}/_deps/a-src {build}/vcpkg_installed/x64-linux/include")
        self.assertEqual(step.env["INCLUDE_EXCLUDE"], "include/xstd/config/*")
        self.assertEqual(step.env["BUILD_DIR"], str(build))
        self.assertEqual(step.env["REPORT"], f"{build}/clang-tidy-report.txt")

    def test_coverage_runs_the_gcovr_script_over_the_legs_tree(self):
        build = self.out / "coverage"
        leg = self.leg("coverage")
        self.assertIn("-DCMAKE_CXX_FLAGS=--coverage -O0", leg.steps[0].argv)
        gcovr = leg.steps[-1]
        self.assertEqual(gcovr.argv, ["bash", f"{ci_local.CPP_CI}/.github/actions/gcovr/gcovr.sh"])
        self.assertEqual(gcovr.env, {"GCOV": "gcov-15", "BUILD_DIR": str(build), "EXCLUDE": "test/.* build/.* examples/.*",
                                     "FAIL_UNDER_LINE": "100", "FAIL_UNDER_BRANCH": "100", "SEARCH_PATHS": str(build)})

    def test_format_and_actionlint_run_as_their_workflows_do(self):
        format_step = self.leg("format").steps[0]
        self.assertIn("xargs -0 clang-format-22 --dry-run --Werror", format_step.argv[2])
        self.assertEqual(format_step.env["PATHS"], "include test examples")
        actionlint = self.leg("actionlint").steps[0]
        self.assertIn('actionlint -shellcheck="$SHELLCHECK" $PATHS', actionlint.argv[2])
        self.assertEqual(actionlint.env, {"PATHS": ".github/workflows/*.yml", "SHELLCHECK": ""})

    def test_consumption_builds_the_models_present(self):
        built = [step.argv[2] for step in self.leg("consumption").steps if step.argv[:2] == ["cmake", "-S"]]
        self.assertEqual(built, [str(self.caller), str(self.caller / "test/consumer/find_package"),
                                 str(self.caller / "test/consumer/fetch_content")])

    def test_dry_run_prints_the_commands(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            ci_local.main(["--caller", str(self.caller), "--dry-run", "gcc-15"])
        self.assertIn("== gcc-15\n", stream.getvalue())
        self.assertIn("-DCMAKE_CXX_COMPILER=g++-15", stream.getvalue())


class ConsumptionThroughVcpkg(Fixture):
    workflows = {"consumption.yml": stub({"consumption": ("consumption", {
        "vcpkg": "true",
        "dependency_repos": "|\n        https://example.com/dep.git 0123abc",
    })})}

    def test_dependencies_install_before_the_library(self):
        root = self.out / "consumption"
        steps = self.leg("consumption").steps
        install = next(step for step in steps if step.argv[1:2] == ["install"])
        self.assertEqual(install.argv, ["/vcpkg/vcpkg", "install", "--triplet", "x64-linux-gcc", "--overlay-triplets",
                                        f"{ci_local.CPP_CI}/.github/actions/vcpkg-overlay/triplets",
                                        f"--x-install-root={root}/vcpkg_installed"])
        self.assertEqual(install.cwd, self.caller)
        clone = next(step for step in steps if step.argv[:2] == ["git", "clone"])
        self.assertEqual(clone.argv[-2:], ["https://example.com/dep.git", str(root / "dep-src-1")])
        find_package = next(step for step in steps if step.argv[2:3] == [str(self.caller / "test/consumer/find_package")])
        self.assertIn(f"-DCMAKE_PREFIX_PATH={root}/install-prefix;{root}/deps-prefix;{root}/vcpkg_installed/x64-linux-gcc",
                      find_package.argv)


class SystemDeps(Fixture):
    deps = "system"

    def boost_test(self, name):
        return [step for step in self.leg(name).steps if step.argv[1:2] == [f"{ci_local.CPP_CI}/tools/boost-test.sh"]]

    def test_no_leg_passes_vcpkg(self):
        for leg in self.legs:
            for step in leg.steps:
                self.assertNotEqual(pathlib.Path(step.argv[0]).name, "vcpkg", leg.name)
                self.assertFalse(any(arg.startswith(("-DCMAKE_TOOLCHAIN_FILE", "-DVCPKG_")) for arg in step.argv), leg.name)
                self.assertFalse(any(key.startswith("VCPKG_") for key in step.env), leg.name)

    def test_dependencies_come_from_the_prefix(self):
        self.assertIn("-DCMAKE_PREFIX_PATH=/usr/local", self.configure("gcc-15").argv)
        self.assertIn("-DCMAKE_PREFIX_PATH=/usr/local", self.configure("clang-tidy-22").argv)
        self.assertIn("-DCMAKE_PREFIX_PATH=/usr/local", self.configure("coverage").argv)

    def test_an_x86_leg_looks_in_the_m32_prefix_first(self):
        self.assertIn("-DCMAKE_PREFIX_PATH=/usr/local/i686;/usr/local", self.configure("i686-15").argv)

    def test_libcxx_trunk_and_x86_build_their_own_boost_test(self):
        expected = {
            "clang-libcxx-22": ("clang++-22", ["-stdlib=libc++"]),
            "gcc-17-svn": ("g++-17", []),
            "clang-24-svn": ("clang++-24", ["--gcc-toolchain=/opt/gcc-latest"]),
            "i686-15": ("g++-15", ["-m32"]),
            "asan-libcxx-22": ("clang++-22", ["-stdlib=libc++"]),
        }
        for name, (cxx, flags) in expected.items():
            package = self.out / f"boost-test-{name}"
            steps = self.boost_test(name)
            self.assertEqual([step.argv for step in steps], [["bash", f"{ci_local.CPP_CI}/tools/boost-test.sh", str(package), cxx, *flags]])
            self.assertIs(self.leg(name).steps[0], steps[0], name)
            self.assertIn(f"-Dboost_unit_test_framework_DIR={package}", self.configure(name).argv)
            self.assertIn("its own Boost.Test", self.leg(name).what)

    def test_stable_and_qualification_link_the_installed_boost_test(self):
        for name in ("gcc-15", "clang-22", "gcc-bmi2-15", "clang-bmi2-22", "gcc-16", "clang-23", "clang-tidy-24-svn",
                     "coverage", "asan-15", "ubsan-gcc-15", "ubsan-clang-22", "implicit-conversion-22"):
            self.assertEqual(self.boost_test(name), [], name)
            self.assertFalse(any("boost_unit_test_framework_DIR" in arg for arg in self.configure(name).argv), name)

    def test_the_rule_reads_the_rung_not_the_name(self):
        self.assertIsNone(ci_local.boost_test_flags("gcc", TABLE["gcc"]["qualification"]))
        self.assertEqual(ci_local.boost_test_flags("gcc", TABLE["gcc"]["development"]), [])
        self.assertEqual(ci_local.boost_test_flags("gcc", TABLE["gcc"]["development"], "x86"), ["-m32"])
        self.assertIsNone(ci_local.boost_test_flags("clang", TABLE["clang"]["qualification"]))
        self.assertEqual(ci_local.boost_test_flags("clang", TABLE["clang"]["stable"] | {"libcxx": True}), ["-stdlib=libc++"])

    def test_a_prefix_elsewhere_reaches_boost_test(self):
        ctx = ci_local.Context(self.caller, self.out, jobs=4, table=TABLE, deps="system", boost_prefix=pathlib.Path("/opt/boost"))
        legs = {leg.name: leg for leg in ci_local.discover(ctx)}
        self.assertEqual(legs["clang-libcxx-22"].steps[0].argv[-3:], ["-stdlib=libc++", "-isystem", "/opt/boost/include"])
        self.assertIn("-DCMAKE_PREFIX_PATH=/opt/boost", legs["gcc-15"].steps[0].argv)

    def test_the_ignorelist_names_the_prefix(self):
        self.assertEqual(self.leg("implicit-conversion-22").steps[0].argv[-1], "src:*/usr/local/include/*")

    def test_listing_and_dry_run_show_the_mode(self):
        self.assertIn("dependencies: --deps system, installed in /usr/local", ci_local.listing(self.ctx, self.legs))
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            ci_local.main(["--caller", str(self.caller), "--deps", "system", "--dry-run", "gcc-15"])
        self.assertIn("# dependencies: --deps system", stream.getvalue())
        self.assertNotIn("VCPKG", stream.getvalue())


class SystemConsumption(Fixture):
    deps = "system"
    workflows = ConsumptionThroughVcpkg.workflows

    def test_vcpkg_true_builds_against_the_installed_packages(self):
        root = self.out / "consumption"
        steps = self.leg("consumption").steps
        self.assertFalse(any(step.argv[1:2] == ["install"] and "vcpkg" in step.argv[0] for step in steps))
        library = next(step for step in steps if step.argv[2:3] == [str(self.caller)])
        self.assertIn(f"-DCMAKE_PREFIX_PATH={root}/deps-prefix;/usr/local", library.argv)
        self.assertFalse(any("VCPKG" in arg for arg in library.argv))
        find_package = next(step for step in steps if step.argv[2:3] == [str(self.caller / "test/consumer/find_package")])
        self.assertIn(f"-DCMAKE_PREFIX_PATH={root}/install-prefix;{root}/deps-prefix;/usr/local", find_package.argv)
        self.assertIn("against /usr/local", self.leg("consumption").what)


class DepsMode(unittest.TestCase):
    def test_vcpkg_by_default(self):
        self.assertEqual(ci_local.deps_mode(None, {}), "vcpkg")

    def test_the_environment_sets_it(self):
        self.assertEqual(ci_local.deps_mode(None, {"CI_LOCAL_DEPS": "system"}), "system")

    def test_the_flag_wins(self):
        self.assertEqual(ci_local.deps_mode("vcpkg", {"CI_LOCAL_DEPS": "system"}), "vcpkg")
        self.assertEqual(ci_local.deps_mode("system", {"CI_LOCAL_DEPS": "vcpkg"}), "system")

    def test_an_unknown_value_is_an_error(self):
        with self.assertRaises(SystemExit):
            ci_local.deps_mode(None, {"CI_LOCAL_DEPS": "conan"})


class SharedNames(Fixture):
    workflows = {
        "gcc.yml": stub({"hardened": ("gcc", {"tiers": "stable"})}),
        "gcc-extra.yml": stub({"hardened": ("gcc", {"tiers": "stable"})}),
        "coverage.yml": stub({"coverage": ("coverage", {}), "coverage_extra": ("coverage", {})}),
    }

    def test_a_shared_name_takes_its_stub_files_name(self):
        self.assertEqual(sorted(self.runnable()), ["coverage", "coverage-coverage-extra", "gcc-extra-hardened-15", "gcc-hardened-15"])


class Run(Fixture):
    workflows = {"actionlint.yml": stub({"actionlint": ("actionlint", {})})}

    def test_a_failing_step_fails_the_leg_and_the_run(self):
        leg = self.leg("actionlint")
        leg.steps = [ci_local.Step(["bash", "-c", "echo hello; exit 3"])]
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = ci_local.run(self.ctx, [leg, ci_local.Leg("after", (), None, "", [ci_local.Step(["true"])])], keep_going=False)
        self.assertEqual(status, 1)
        self.assertRegex(stream.getvalue(), r"FAIL actionlint +\d+s  .*actionlint\.log\nskip after\n")
        log = (self.out / "actionlint.log").read_text()
        self.assertIn("hello\n", log)
        self.assertIn("exit status 3", log)

    def test_keep_going_runs_the_rest(self):
        legs = [ci_local.Leg("first", (), None, "", [ci_local.Step(["false"])]),
                ci_local.Leg("second", (), None, "", [ci_local.Step(["true"])])]
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = ci_local.run(self.ctx, legs, keep_going=True)
        self.assertEqual(status, 1)
        self.assertRegex(stream.getvalue(), r"pass second +\d+s")


class Helpers(unittest.TestCase):
    def test_build_jobs_is_capped_by_memory_and_cpus(self):
        with tempfile.NamedTemporaryFile("w", suffix=".meminfo") as meminfo:
            meminfo.write("MemTotal: 99 kB\nMemAvailable: 9437184 kB\n")
            meminfo.flush()
            self.assertEqual(ci_local.build_jobs(4096, meminfo.name), min(2, ci_local.os.cpu_count()))
            self.assertEqual(ci_local.build_jobs(65536, meminfo.name), 1)

    def test_names(self):
        self.assertEqual(ci_local.base_name("clang_libcxx_ladder"), "clang-libcxx")
        self.assertEqual(ci_local.base_name("gcc_bmi2"), "gcc-bmi2")
        self.assertEqual(ci_local.label_name("17-SVN"), "17-svn")

    def test_workflow_booleans(self):
        self.assertTrue(ci_local.truthy(True))
        self.assertTrue(ci_local.truthy("true"))
        self.assertFalse(ci_local.truthy("false"))
        self.assertFalse(ci_local.truthy(False))


if __name__ == "__main__":
    unittest.main()
