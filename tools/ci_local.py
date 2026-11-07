#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Run a caller's Linux CI legs locally, derived from the caller's own workflow stubs.

    ci_local.py [--caller DIR] [--out DIR] [--deps {vcpkg,system}] [--list] [--dry-run] [--keep-going] [--stable]
                [LEG_OR_GLOB ...]

The stubs are the single source. Every job in the caller's .github/workflows/*.yml whose uses: names
rhalbersma/cpp-ci/.github/workflows/<name>.yml@<sha> is a leg source: its with: inputs override that shared
workflow's input defaults, which are read from this checkout's copy of the workflow, and its tiers resolve through
.github/actions/toolchain/toolchains.json as the toolchain action resolves them. A caller's own jobs, those that call
no cpp-ci workflow, are not this runner's and appear nowhere.

Each shared workflow maps to the legs its Linux runner runs, with the same cmake arguments, environment and scripts:

    gcc.yml, clang.yml, clang-libc++.yml   one leg per tier: configure through vcpkg, build, ctest
    clang-tidy.yml                         one leg per tier: its configure, then the clang-tidy action's script
    coverage.yml                           the --coverage build and tests, then the gcovr action's script
    sanitizers.yml                         the sanitizer-legs action's crossing, each leg built and tested
    consumption.yml                        the library installed, then each consumption model built and run
    clang-format.yml                       clang-format --dry-run --Werror over the stub's paths
    actionlint.yml                         actionlint over the stub's workflow glob
    anything else                          listed as skipped, with the reason

A leg is named for the stub's job and the rung's label: the job id loses a _ladder suffix and takes hyphens for
underscores, so gcc_ladder gives gcc-15, gcc-16 and gcc-17-svn, i686 gives i686-15 and gcc_bmi2 gives gcc-bmi2-15. A
sanitizer leg takes the sanitizer's own id, as asan-15 or ubsan-clang-22. The one-leg workflows are actionlint,
format, coverage and consumption, suffixed with the job where a caller has two stubs of one and the job is named
otherwise. A name two stubs would still share is prefixed with the stub's file name.

Legs run cheapest first: actionlint and format, the suite legs at the stable tier, the suite legs at the other tiers,
clang-tidy, coverage, the sanitizers, then consumption. The run stops at the first leg that fails unless --keep-going,
and ends with one line per leg. Each leg builds in <out>/<leg> and logs to <out>/<leg>.log, and a second run reuses
the build tree, as an incremental build; delete it for a clean one.

--deps picks where a caller's vcpkg.json dependencies come from, CI_LOCAL_DEPS where the flag is not given. vcpkg, the
default, builds them as CI does. system takes what is installed, for a machine whose network refuses the source
archives vcpkg's Boost ports download: no leg passes vcpkg's toolchain, triplet, overlay or features, or its
environment, and each finds its dependencies in BOOST_PREFIX, else /usr/local, where tools/setup-toolchain.sh --boost
builds Boost with the stable GCC; an x86 leg looks in <prefix>/i686 first, where it builds Boost again at -m32. A leg
whose standard library or target differs from the stable GCC's, through libc++, a trunk libstdc++ or -m32, first
builds a Boost.Test of its own with tools/boost-test.sh into <out>/boost-test-<leg> and links that; a failed build
fails the leg. The qualification tier's libstdc++ links the installed one. A consumption stub with vcpkg: true builds
against the installed packages, and the implicit-conversion ignorelist names <prefix>/include as foreign code.

What differs from CI, and why:

- The libc++ legs run at the stable rung alone. Apt's libc++ packages exclude one another, so a machine holds one, and
  CI gives each libc++ leg a runner of its own. The sanitizer legs likewise run at the stable rung; CI runs the rest.
- Every GCC is installed here, where a CI job installs only the one it pairs with Clang, and Clang picks the newest
  GCC it finds. A Clang rung whose cxxflags are empty is pinned to its own libstdc++ with --gcc-install-dir, in the
  project's flags and in VCPKG_CLANG_FLAGS; the clang-tidy legs pin the gcc_tier's. clang-tidy's header pass runs
  outside the compile database, so there it still sees the newest libstdc++.
- With --deps vcpkg, vcpkg installs from the CMake toolchain's manifest install into the leg's own tree, never into
  the caller's checkout, against vcpkg's default binary cache (VCPKG_DEFAULT_BINARY_CACHE, else
  ~/.cache/vcpkg/archives), which every leg and every caller share; the ABI hash in each archive's name keeps compilers
  apart. vcpkg is the one in VCPKG_ROOT, else ~/.local/share/cpp-ci/vcpkg, where tools/setup-toolchain.sh checks out
  the pinned commit.
- Every build runs as many jobs as available memory holds at build_memory_mib each, coverage's included, and a pull
  request's reduce_on_pr never applies: a local run is a push.
"""

import argparse
import dataclasses
import fnmatch
import functools
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    raise SystemExit("ci_local.py needs PyYAML: pip install pyyaml") from None

CPP_CI = Path(__file__).resolve().parent.parent
LOCAL_BIN = Path.home() / ".local" / "bin"
USES = re.compile(r"^rhalbersma/cpp-ci/\.github/workflows/([A-Za-z0-9_.+-]+)\.yml@([0-9a-f]+)$")
WORKSPACE = re.compile(r"\$\{\{\s*github\.workspace\s*\}\}")
TIERS = ("stable", "qualification", "development")
SUITES = {"gcc": "gcc", "clang": "clang", "clang-libc++": "libcxx"}
SINGLE = {"actionlint": "actionlint", "clang-format": "format", "coverage": "coverage", "consumption": "consumption"}
MODELS = ("find_package", "add_subdirectory", "fetch_content")
BINARY_SOURCES = "clear;default,readwrite"
DEPS = ("vcpkg", "system")
DEFAULT_PREFIX = "/usr/local"

# Run order: the cheapest checks first, so a slip fails in seconds rather than after an hour of compiling. A leg's
# order is (group, tier, place within the group, stub file, stub position).
ACTIONLINT, FORMAT, SUITE_STABLE, SUITE_OTHER, TIDY, COVERAGE, SANITIZERS, CONSUMPTION, SKIPPED = range(9)

SKIP_REASONS = {
    "apple-clang": "runs on macOS, which no local Linux runner reproduces",
    "ios": "runs on macOS, which no local Linux runner reproduces",
    "visual-studio": "runs MSVC or clang-cl on Windows, which no local Linux runner reproduces",
    "msvc-analyze": "runs MSVC's /analyze on Windows, which no local Linux runner reproduces",
    "mingw": "runs MinGW on Windows, which no local Linux runner reproduces",
    "emscripten": "CI-only: tools/setup-toolchain.sh installs no Emscripten SDK",
    "android-ndk": "CI-only: tools/setup-toolchain.sh installs no Android NDK",
    "s390x": "CI-only: an s390x cross build run under QEMU on its own runner",
    "codeql": "CI-only: its results go to GitHub code scanning",
    "compile-budget": "CI-only: it compares against budgets CI records",
    "scorecard": "CI-only: OpenSSF Scorecard reads the repository's GitHub settings",
}


@dataclasses.dataclass
class Step:
    """One command of a leg: its arguments, the environment it adds, and where it runs."""

    argv: list[str]
    env: dict[str, str] = dataclasses.field(default_factory=dict)
    cwd: Path | None = None


@dataclasses.dataclass
class Leg:
    """A local leg, runnable unless skip gives the reason it is not."""

    name: str
    order: tuple
    tier: str | None
    what: str
    steps: list[Step] = dataclasses.field(default_factory=list)
    skip: str = ""
    stub: tuple = ()


@dataclasses.dataclass
class Context:
    """What every leg is built from: the caller, where legs build, this checkout, vcpkg, the build parallelism, the
    toolchain table, and whether dependencies come from vcpkg or from what is installed in boost_prefix."""

    caller: Path
    out: Path
    cpp_ci: Path = CPP_CI
    vcpkg_root: Path = Path(os.environ.get("VCPKG_ROOT") or Path.home() / ".local/share/cpp-ci/vcpkg")
    jobs: int | None = None
    table: dict | None = None
    deps: str = "vcpkg"
    boost_prefix: Path = Path(os.environ.get("BOOST_PREFIX") or DEFAULT_PREFIX)

    def __post_init__(self):
        if self.table is None:
            self.table = json.loads((self.cpp_ci / ".github/actions/toolchain/toolchains.json").read_text())

    def leg_dir(self, name):
        return self.out / name

    def toolchain_file(self):
        return str(self.vcpkg_root / "scripts/buildsystems/vcpkg.cmake")

    def overlay(self):
        return str(self.cpp_ci / ".github/actions/vcpkg-overlay/triplets")

    def script(self, action):
        return str(self.cpp_ci / f".github/actions/{action}/{action}.sh")

    def system(self):
        return self.deps == "system"

    def prefixes(self, arch="x64"):
        """Where a leg finds installed dependencies: the prefix, and for an x86 leg its -m32 copy first."""
        return f"{self.boost_prefix}/i686;{self.boost_prefix}" if arch == "x86" else str(self.boost_prefix)


def build_jobs(memory_mib, meminfo="/proc/meminfo"):
    """As many parallel compiles as available memory holds at memory_mib each, at least one and at most one per CPU."""
    available_kib = 0
    for line in Path(meminfo).read_text().splitlines():
        if line.startswith("MemAvailable:"):
            available_kib = int(line.split()[1])
    return max(1, min(os.cpu_count() or 1, available_kib // 1024 // int(memory_mib)))


def truthy(value):
    """A workflow boolean as a stub may spell it, true or "true"."""
    return value is True or str(value).lower() == "true"


def stubs(caller):
    """Every job calling a cpp-ci workflow: (file, job id, workflow, with:, pinned sha), in file and job order."""
    found = []
    for path in sorted((caller / ".github/workflows").glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            match = USES.match(str((job or {}).get("uses", "")))
            if match:
                found.append((path.name, job_id, match[1], job.get("with") or {}, match[2]))
    return found


@functools.cache
def defaults(cpp_ci, workflow):
    """The shared workflow's workflow_call input defaults, from this checkout's copy; PyYAML reads on: as True."""
    path = cpp_ci / f".github/workflows/{workflow}.yml"
    if not path.is_file():
        return {}
    doc = yaml.safe_load(path.read_text()) or {}
    trigger = doc.get(True) or doc.get("on") or {}
    declared = (trigger.get("workflow_call") or {}).get("inputs") or {}
    return {name: spec.get("default", "") for name, spec in declared.items()}


def inputs(ctx, workflow, given):
    """The stub's with: over the shared workflow's defaults, with github.workspace read as the caller's root."""
    merged = dict(defaults(ctx.cpp_ci, workflow))
    for name, value in given.items():
        merged[name] = WORKSPACE.sub(str(ctx.caller), value) if isinstance(value, str) else value
    return merged


def csv(value):
    """A comma-separated input as its items, with whitespace dropped as the workflows drop it."""
    return [item for item in (part.replace(" ", "").strip() for part in str(value or "").split(",")) if item]


def rungs(ctx, family, tiers):
    """The (tier, entry) pairs a family fills among the tiers asked for; libc++ climbs Clang's ladder."""
    table = ctx.table["clang" if family == "libcxx" else family]
    return [(tier, table[tier]) for tier in csv(tiers) if tier in table]


def rung(ctx, family, tier, stub):
    """The one rung a single-rung input names, which must be filled."""
    entry = ctx.table[family].get(tier)
    if entry is None:
        raise SystemExit(f"{stub[0]}/{stub[1]}: {family} has no {tier} rung")
    return entry


def build_type(types):
    """Debug where the stub builds it, since it checks the most; else the first type it names."""
    listed = csv(types)
    return "Debug" if "Debug" in listed or not listed else listed[0]


def libstdcxx_flag(gcc_version, gcc_trunk):
    """The flag that pins Clang to one GCC's libstdc++, where a CI runner would hold that GCC alone."""
    if truthy(gcc_trunk):
        return "--gcc-toolchain=/opt/gcc-latest"
    return f"--gcc-install-dir=/usr/lib/gcc/x86_64-linux-gnu/{gcc_version}"


def clang_flags(entry):
    """A Clang rung's cxxflags, or, where they are empty, the flag pinning it to its paired libstdc++."""
    return entry["cxxflags"] or libstdcxx_flag(entry["gcc_version"], entry["gcc_trunk"])


def features_arg(features):
    """VCPKG_MANIFEST_FEATURES as the workflows spell it, a CMake list, or nothing when no feature is named."""
    names = ";".join(csv(features))
    return [f"-DVCPKG_MANIFEST_FEATURES={names}"] if names else []


def vcpkg_args(ctx, source, triplet, features, overlay=True, arch="x64"):
    """The dependency arguments, where the source has a manifest: vcpkg's toolchain file, triplet, features and overlay,
    or under --deps system the installed prefixes alone."""
    if not (source / "vcpkg.json").is_file():
        return []
    if ctx.system():
        return [f"-DCMAKE_PREFIX_PATH={ctx.prefixes(arch)}"]
    args = [f"-DCMAKE_TOOLCHAIN_FILE={ctx.toolchain_file()}", f"-DVCPKG_TARGET_TRIPLET={triplet}", *features_arg(features)]
    return args + ([f"-DVCPKG_OVERLAY_TRIPLETS={ctx.overlay()}"] if overlay else [])


def boost_test_flags(family, entry, arch="x64"):
    """The flags choosing a leg's standard library or target where these differ from the stable GCC's, whose Boost.Test
    --deps system finds installed: libc++, a trunk libstdc++ or -m32. None where the installed one links."""
    if family == "libcxx" or entry.get("libcxx") is True:
        flags = ["-stdlib=libc++"]
    elif family == "gcc" and truthy(entry.get("trunk")):
        flags = []
    elif family == "clang" and truthy(entry.get("gcc_trunk")):
        flags = clang_flags(entry).split()
    elif arch == "x86":
        flags = []
    else:
        return None
    return flags + (["-m32"] if arch == "x86" else [])


def boost_test(ctx, source, name, cxx, flags):
    """Under --deps system, where flags is not None: the step building the leg's own Boost.Test, and the -D finding it.
    Nothing otherwise, or where the source has no manifest to need it."""
    if not ctx.system() or flags is None or not (source / "vcpkg.json").is_file():
        return [], []
    package = ctx.out / f"boost-test-{name}"
    search = [] if str(ctx.boost_prefix) == DEFAULT_PREFIX else ["-isystem", f"{ctx.boost_prefix}/include"]
    step = Step(["bash", str(ctx.cpp_ci / "tools/boost-test.sh"), str(package), cxx, *flags, *search])
    return [step], [f"-Dboost_unit_test_framework_DIR={package}"]


def deps_shown(ctx, triplet, own_boost_test):
    """Where a leg's dependencies come from, for its description: the triplet, or the installed prefix."""
    if not ctx.system():
        return triplet
    return f"installed in {ctx.boost_prefix}" + (", its own Boost.Test" if own_boost_test else "")


def tail_args(given):
    """The module scan and the Debug flags, ahead of cmake_args so a caller's own -D for either wins."""
    args = [f"-DCMAKE_CXX_SCAN_FOR_MODULES={'ON' if truthy(given['scan_for_modules']) else 'OFF'}"]
    if given.get("debug_flags"):
        args.append(f"-DCMAKE_CXX_FLAGS_DEBUG={given['debug_flags']}")
    return args + str(given.get("cmake_args") or "").split()


def build_and_test(ctx, build, given, env, options=None):
    """The Build and Test steps: a memory-capped parallel build, then ctest."""
    jobs = ctx.jobs or build_jobs(given.get("build_memory_mib") or 4096)
    return [
        Step(["cmake", "--build", str(build), "-v", "--parallel", str(jobs)], env),
        Step(["ctest", "--test-dir", str(build), "--output-on-failure"], {**env, **(options or {})}),
    ]


def base_name(job_id):
    """A job id as a leg name: no _ladder suffix, hyphens for underscores."""
    return job_id.removesuffix("_ladder").replace("_", "-").lower()


def label_name(label):
    """A rung label as a leg name's suffix: 17-SVN gives 17-svn."""
    return label.lower().replace(" ", "-")


def suite_legs(ctx, stub, given, family, position):
    """gcc.yml, clang.yml or clang-libc++.yml: one leg per tier, libc++ at the stable tier alone."""
    file, job_id = stub[:2]
    btype = build_type(given["build_types"])
    arch = given.get("arch", "x64")
    if arch not in ("x64", "x86"):
        raise SystemExit(f"{file}/{job_id}: arch is x64 or x86, not '{arch}'")
    legs = []
    for tier, entry in rungs(ctx, family, given["tiers"]):
        name = f"{base_name(job_id)}-{label_name(entry['label'])}"
        group = SUITE_STABLE if tier == "stable" else SUITE_OTHER
        order = (group, TIERS.index(tier), list(SUITES.values()).index(family), file, position)
        build = ctx.leg_dir(name)
        version = entry["version"]
        env = {"VCPKG_BINARY_SOURCES": BINARY_SOURCES}
        if family == "gcc":
            cc, cxx = f"gcc-{version}", f"g++-{version}"
            arch_flags = "-m32" if arch == "x86" else ""
            triplet = "x86-linux-gcc" if arch == "x86" else given["vcpkg_triplet"]
            # -m32 on the C compiler too, so CMake's own probes see the target.
            flags = [f"-DCMAKE_C_FLAGS={arch_flags}",
                     f"-DCMAKE_CXX_FLAGS={arch_flags + ' ' if arch_flags else ''}{given['cxx_flags']}"]
            env["VCPKG_GCC_VERSION"] = version
        elif family == "clang":
            cc, cxx = f"clang-{version}", f"clang++-{version}"
            triplet = given["vcpkg_triplet"]
            flags = [f"-DCMAKE_CXX_FLAGS={clang_flags(entry)} {given['cxx_flags']}"]
            env |= {"VCPKG_CLANG_VERSION": version, "VCPKG_CLANG_FLAGS": clang_flags(entry)}
        else:
            cc, cxx = f"clang-{version}", f"clang++-{version}"
            triplet = given["vcpkg_triplet"]
            flags = [f"-DCMAKE_CXX_FLAGS=-stdlib=libc++ {given['cxx_flags']}"]
            env["VCPKG_CLANG_VERSION"] = version
        extra = " ".join(part for part in (given["cxx_flags"], given["cmake_args"]) if part)
        local = not (family == "libcxx" and tier != "stable")
        prepare, found = boost_test(ctx, ctx.caller, name, cxx, boost_test_flags(family, entry, arch)) if local else ([], [])
        what = f"{file}/{job_id}: {cxx} {btype}{' ' + extra if extra else ''}, {deps_shown(ctx, triplet, prepare)}"
        leg = Leg(name, order, tier, what, stub=stub)
        if not local:
            leg.skip = "libc++ runs at the stable rung locally: apt's libc++ packages exclude one another; CI runs this rung"
            leg.order = (SKIPPED, 0, 0, file, position)
        else:
            configure = ["cmake", "-S", str(ctx.caller), "-B", str(build), "-G", given["generator"],
                         f"-DCMAKE_BUILD_TYPE={btype}", f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}", *flags,
                         *vcpkg_args(ctx, ctx.caller, triplet, given["vcpkg_features"], arch=arch), *found, *tail_args(given)]
            leg.steps = [*prepare, Step(configure, env), *build_and_test(ctx, build, given, env)]
        legs.append(leg)
    return legs


def local_paths(value, ctx, build):
    """A stub's paths into its CI build tree, rebased onto the leg's: build/ and vcpkg_installed/ name the leg's tree."""
    rebased = []
    ci_build = f"{ctx.caller}/build/"
    for token in str(value or "").split():
        if token.startswith(ci_build):
            token = f"{build}/{token[len(ci_build):]}"
        elif token == "build" or token.startswith("build/"):
            token = f"{build}{token[len('build'):]}"
        elif token.startswith("vcpkg_installed/"):
            token = f"{build}/{token}"
        if token not in rebased:
            rebased.append(token)
    return " ".join(rebased)


def tidy_legs(ctx, stub, given, position):
    """clang-tidy.yml: one leg per tier, its Debug configure, then the clang-tidy action's script."""
    file, job_id = stub[:2]
    libstdcxx = rung(ctx, "gcc", given["gcc_tier"], stub)
    pairing = libstdcxx_flag(libstdcxx["version"], libstdcxx["trunk"])
    legs = []
    for tier, entry in rungs(ctx, "clang", given["tiers"]):
        name = f"{base_name(job_id)}-{label_name(entry['label'])}"
        build = ctx.leg_dir(name)
        version = entry["version"]
        env = {"VCPKG_BINARY_SOURCES": BINARY_SOURCES}
        configure = ["cmake", "-S", str(ctx.caller), "-B", str(build), "-G", given["generator"],
                     "-DCMAKE_CXX_SCAN_FOR_MODULES=OFF", "-DCMAKE_BUILD_TYPE=Debug",
                     f"-DCMAKE_C_COMPILER=clang-{version}", f"-DCMAKE_CXX_COMPILER=clang++-{version}",
                     f"-DCMAKE_CXX_FLAGS={pairing}", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                     *vcpkg_args(ctx, ctx.caller, given["vcpkg_triplet"], given["vcpkg_features"], overlay=False),
                     *str(given.get("cmake_args") or "").split()]
        tidy_env = {
            "CLANG_TIDY_VERSION": version,
            "BUILD_DIR": str(build),
            "INCLUDE_DIR": given["include_dir"],
            "INCLUDE_EXCLUDE": given["include_exclude"] or given["header_exclude"],
            "SRC_DIR": given["src_dir"],
            "TEST_DIR": given["test_dir"],
            "SELF_SUFFICIENCY_REGEX": local_paths(given["self_sufficiency_regex"], ctx, build),
            "SOURCES_REGEX": given["sources_regex"],
            "ADAPTED_DIRS": local_paths(given["adapted_dirs"], ctx, build),
            "REPORT": str(build / "clang-tidy-report.txt"),
        }
        what = f"{file}/{job_id}: clang-tidy-{version} on a Debug configure, libstdc++ {libstdcxx['label']}"
        legs.append(Leg(name, (TIDY, TIERS.index(tier), 0, file, position), tier, what, [
            Step(configure, env),
            Step(["bash", ctx.script("clang-tidy")], tidy_env, ctx.caller),
        ], stub=stub))
    return legs


def coverage_leg(ctx, stub, given, name, position):
    """coverage.yml: the stable GCC's --coverage build and tests, then the gcovr action's script."""
    file, job_id = stub[:2]
    entry = rung(ctx, "gcc", given["gcc_tier"], stub)
    version = entry["version"]
    source = (ctx.caller / given["source_dir"]).resolve()
    build = ctx.leg_dir(name)
    env = {"VCPKG_BINARY_SOURCES": BINARY_SOURCES}
    configure = ["cmake", "-S", str(source), "-B", str(build), "-G", given["generator"], "-DCMAKE_BUILD_TYPE=Debug",
                 f"-DCMAKE_C_COMPILER=gcc-{version}", f"-DCMAKE_CXX_COMPILER=g++-{version}",
                 "-DCMAKE_CXX_FLAGS=--coverage -O0", "-DCMAKE_EXE_LINKER_FLAGS=--coverage",
                 *vcpkg_args(ctx, source, given["vcpkg_triplet"], given["vcpkg_features"], overlay=False), *tail_args(given)]
    gcovr_env = {
        "GCOV": f"gcov-{version}",
        "BUILD_DIR": str(build),
        "EXCLUDE": given["exclude"],
        "FAIL_UNDER_LINE": str(given["fail_under_line"]),
        "FAIL_UNDER_BRANCH": str(given["fail_under_branch"]),
        "SEARCH_PATHS": str(build),
    }
    what = (f"{file}/{job_id}: g++-{version} --coverage, gcovr at {given['fail_under_line']}% of lines and "
            f"{given['fail_under_branch']}% of branches")
    # The counters of an earlier run would add to this one's, or fail gcov on a stamp that no longer matches.
    clear = Step(["find", str(build), "-name", "*.gcda", "-delete"])
    build_step, test_step = build_and_test(ctx, build, given, env)
    return Leg(name, (COVERAGE, 0, 0, file, position), given["gcc_tier"], what, [
        Step(configure, env), build_step, clear, test_step, Step(["bash", ctx.script("gcovr")], gcovr_env, source),
    ], stub=stub)


def matrix(ctx, family, tiers):
    """The toolchain action's matrix output for one family at the tiers asked for, in Debug."""
    table = ctx.table[family]
    return {"include": [table[tier] | {"tier": tier, "build_type": "Debug"} for tier in csv(tiers) if tier in table]}


def cross(ctx, gcc, clang, libcxx):
    """The sanitizer-legs action's crossing of its leg table with the rungs, by its own script."""
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_OUTPUT", "GITHUB_ACTIONS")}
    env |= {"GCC_RUNGS": json.dumps(gcc), "CLANG_RUNGS": json.dumps(clang), "LIBCXX_RUNGS": json.dumps(libcxx) if libcxx else ""}
    run = subprocess.run(["bash", ctx.script("sanitizer-legs")], env=env, check=True, capture_output=True, text=True)
    return json.loads(run.stdout)["include"]


IGNORELIST = ("src:*/include/c++/*", "src:*/lib/clang/*/include/*", "src:*/vcpkg_installed/*", "src:*/vcpkg/buildtrees/*",
              "src:*/usr/include/*")


def sanitizer_legs(ctx, stub, given, position):
    """sanitizers.yml: each crossed leg built and tested as its build job does; the stable rung alone locally."""
    file, job_id = stub[:2]
    libcxx = matrix(ctx, "clang", given["libcxx_tiers"]) if csv(given["libcxx_tiers"]) else None
    entries = cross(ctx, matrix(ctx, "gcc", given["gcc_tiers"]), matrix(ctx, "clang", given["clang_tiers"]), libcxx)
    legs = []
    for index, entry in enumerate(entries):
        name = f"{entry['id']}-{label_name(entry['label'])}"
        build = ctx.leg_dir(name)
        version = entry["version"]
        is_libcxx = entry.get("libcxx") is True
        what = f"{file}/{job_id}: {entry['name']} / {entry['compiler']} {entry['label']}"
        leg = Leg(name, (SANITIZERS, 0, index, file, position), entry["tier"], what, stub=stub)
        if entry["tier"] != "stable":
            leg.skip = "sanitizers run at the stable rung locally; CI runs this rung"
            leg.order = (SKIPPED, 0, 0, file, position)
            legs.append(leg)
            continue
        env = {"VCPKG_BINARY_SOURCES": BINARY_SOURCES}
        rung_flags = ""
        if entry["family"] == "gcc":
            cc, cxx, triplet = f"gcc-{version}", f"g++-{version}", given["vcpkg_gcc_triplet"]
            env["VCPKG_GCC_VERSION"] = version
        else:
            cc, cxx = f"clang-{version}", f"clang++-{version}"
            env["VCPKG_CLANG_VERSION"] = version
            if is_libcxx:
                triplet = given["vcpkg_libcxx_triplet"]
            else:
                triplet, rung_flags = given["vcpkg_clang_triplet"], clang_flags(entry)
                env["VCPKG_CLANG_FLAGS"] = rung_flags
        steps, found = boost_test(ctx, ctx.caller, name, cxx, boost_test_flags("libcxx" if is_libcxx else entry["family"], entry))
        if steps:
            leg.what += ", its own Boost.Test"
        ignorelist = ""
        if entry.get("ignore_foreign_code"):
            ignorelist_file = build / "sanitizer-ignorelist.txt"
            ignorelist = f"-fsanitize-ignorelist={ignorelist_file}"
            installed = [f"src:*{ctx.boost_prefix}/include/*"] if ctx.system() else []
            steps.append(Step(["bash", "-c", 'mkdir -p "${1%/*}" && printf "%s\\n" "${@:2}" > "$1"', "ignorelist",
                               str(ignorelist_file), *IGNORELIST, *installed]))
        sanitizer_flags = f"{entry['flags']} {rung_flags}"
        configure = ["cmake", "-S", str(ctx.caller), "-B", str(build), "-G", given["generator"], "-DCMAKE_BUILD_TYPE=Debug",
                     f"-DCMAKE_C_COMPILER={cc}", f"-DCMAKE_CXX_COMPILER={cxx}",
                     f"-DCMAKE_CXX_FLAGS={sanitizer_flags} {ignorelist} {given['cxx_flags']}",
                     f"-DCMAKE_EXE_LINKER_FLAGS={sanitizer_flags}",
                     *vcpkg_args(ctx, ctx.caller, triplet, given["vcpkg_features"]), *found, *tail_args(given)]
        options = dict(option.split("=", 1) for option in entry["options"].split())
        leg.steps = [*steps, Step(configure, env), *build_and_test(ctx, build, given, env, options)]
        legs.append(leg)
    return legs


def consumption_leg(ctx, stub, given, name, position):
    """consumption.yml: install the library, then build and run each consumption model the caller has."""
    file, job_id = stub[:2]
    entry = rung(ctx, "gcc", given["gcc_tier"], stub)
    version = entry["version"]
    cc, cxx = f"gcc-{version}", f"g++-{version}"
    root = ctx.leg_dir(name)
    consumers = ctx.caller / given["consumer_dir"]
    scan = f"-DCMAKE_CXX_SCAN_FOR_MODULES={'ON' if truthy(given['scan_for_modules']) else 'OFF'}"
    generator = given["generator"]
    use_vcpkg = truthy(given["vcpkg"])
    triplet = given["vcpkg_triplet"]
    env = {"VCPKG_BINARY_SOURCES": BINARY_SOURCES, "VCPKG_GCC_VERSION": version}
    models = [model for model in MODELS if (consumers / model / "CMakeLists.txt").is_file()]
    # A fresh tree each run, as on a runner: an install prefix left from an earlier run would satisfy find_package.
    steps = [Step(["rm", "-rf", str(root)]), Step(["mkdir", "-p", str(root)])]
    if not models:
        message = f"no consumer model under '{given['consumer_dir']}': expected at least one of {', '.join(MODELS)}"
        steps.append(Step(["bash", "-c", 'echo "error: $1" >&2; exit 1', "consumption", message]))
    # Under --deps system, vcpkg: true finds the packages installed in the prefix rather than installing its own.
    through_vcpkg = use_vcpkg and not ctx.system()
    installed = ctx.boost_prefix if ctx.system() else root / "vcpkg_installed" / triplet
    if through_vcpkg:
        steps.append(Step([str(ctx.vcpkg_root / "vcpkg"), "install", "--triplet", triplet, "--overlay-triplets", ctx.overlay(),
                           f"--x-install-root={root / 'vcpkg_installed'}"], env, ctx.caller))
    deps = root / "deps-prefix" if str(given["dependency_repos"]).strip() else None
    search = ";".join(str(path) for path in (deps, installed if use_vcpkg else None) if path)
    for index, line in enumerate((line for line in str(given["dependency_repos"]).splitlines() if line.strip()), 1):
        fields = line.split()
        if len(fields) != 2:
            raise SystemExit(f"{file}/{job_id}: dependency_repos wants '<url> <revision>' per line, got '{line}'")
        url, revision = fields
        source, build = root / f"dep-src-{index}", root / f"dep-build-{index}"
        steps += [
            Step(["git", "clone", "--no-checkout", "--quiet", url, str(source)]),
            Step(["git", "-C", str(source), "checkout", "--detach", "--quiet", revision]),
            Step(["cmake", "-S", str(source), "-B", str(build), "-G", generator, f"-DCMAKE_C_COMPILER={cc}",
                  f"-DCMAKE_CXX_COMPILER={cxx}", f"-DCMAKE_PREFIX_PATH={search}", scan, "-DBUILD_TESTING=OFF"], env),
            Step(["cmake", "--install", str(build), "--prefix", str(deps)]),
        ]
    library = [f"-DCMAKE_TOOLCHAIN_FILE={ctx.toolchain_file()}", f"-DVCPKG_TARGET_TRIPLET={triplet}",
               f"-DVCPKG_OVERLAY_TRIPLETS={ctx.overlay()}"] if through_vcpkg else []
    library_search = search if use_vcpkg and ctx.system() else str(deps or "")
    common = search
    prefix = root / "install-prefix"
    steps += [
        Step(["cmake", "-S", str(ctx.caller), "-B", str(root / "build"), "-G", generator, f"-DCMAKE_C_COMPILER={cc}",
              f"-DCMAKE_CXX_COMPILER={cxx}", "-DBUILD_TESTING=OFF", scan, *library,
              *([f"-DCMAKE_PREFIX_PATH={library_search}"] if library_search else []), *str(given["cmake_args"] or "").split()], env),
        Step(["cmake", "--install", str(root / "build"), "--prefix", str(prefix)]),
    ]
    for model in models:
        build = root / f"build-{model.replace('_', '-')}"
        if model == "find_package":
            prefix_args = [f"-DCMAKE_PREFIX_PATH={prefix}{';' + common if common else ''}"]
        else:
            prefix_args = [f"-DCMAKE_PREFIX_PATH={common}"] if common else []
        configure = ["cmake", "-S", str(consumers / model), "-B", str(build), "-G", generator, f"-DCMAKE_CXX_COMPILER={cxx}"]
        configure += [*prefix_args, scan] if model == "find_package" else [scan, *prefix_args]
        steps += [Step(configure, env), Step(["cmake", "--build", str(build), "-v"]), Step([str(build / "main")])]
    source = (" through vcpkg" if through_vcpkg else f" against {ctx.boost_prefix}") if use_vcpkg else ""
    what = f"{file}/{job_id}: {cxx}, {' '.join(models) or 'no model'}{source}"
    return Leg(name, (CONSUMPTION, 0, 0, file, position), given["gcc_tier"], what, steps, stub=stub)


def format_leg(ctx, stub, given, name, position):
    """clang-format.yml: the tier's clang-format over the stub's paths, reporting rather than rewriting."""
    file, job_id = stub[:2]
    version = rung(ctx, "clang", given["tier"], stub)["version"]
    script = ('set -euo pipefail; read -ra roots <<< "$PATHS"; '
              f'find "${{roots[@]}}" -regex "$FILE_REGEX" -print0 | xargs -0 clang-format-{version} --dry-run --Werror')
    what = f"{file}/{job_id}: clang-format-{version} over {given['paths']}"
    return Leg(name, (FORMAT, 0, 0, file, position), given["tier"], what, [
        Step(["bash", "-c", script], {"PATHS": given["paths"], "FILE_REGEX": given["file_regex"]}, ctx.caller),
    ], stub=stub)


def actionlint_leg(ctx, stub, given, name, position):
    """actionlint.yml: actionlint over the stub's glob, with its -shellcheck setting."""
    file, job_id = stub[:2]
    paths = given["paths"] or ".github/workflows/*.yml"
    script = 'set -euo pipefail; actionlint -shellcheck="$SHELLCHECK" $PATHS'
    what = f"{file}/{job_id}: actionlint over {paths} (CI builds {given['actionlint_version']})"
    return Leg(name, (ACTIONLINT, 0, 0, file, position), None, what, [
        Step(["bash", "-c", script], {"PATHS": paths, "SHELLCHECK": given["shellcheck"]}, ctx.caller),
    ], stub=stub)


def skipped_leg(stub, position):
    """A shared workflow with no local Linux counterpart, listed with the reason."""
    file, job_id, workflow = stub[:3]
    reason = SKIP_REASONS.get(workflow, f"CI-only: no local runner for {workflow}.yml")
    return Leg(base_name(job_id), (SKIPPED, 0, 0, file, position), None, f"{file}/{job_id}", skip=reason, stub=stub)


def unique(legs):
    """Names two stubs would share, prefixed with the stub's file name; any still shared is an error."""
    for attempt in range(2):
        seen = {}
        for leg in legs:
            seen.setdefault(leg.name, []).append(leg)
        shared = [group for group in seen.values() if len(group) > 1]
        if not shared:
            return legs
        if attempt == 1:
            names = ", ".join(sorted({group[0].name for group in shared}))
            raise SystemExit(f"leg names shared between stubs: {names}")
        for group in shared:
            for leg in group:
                leg.name = f"{Path(leg.stub[0]).stem}-{leg.name}"
    return legs


def discover(ctx):
    """Every leg the caller's stubs ask for, runnable or skipped, in run order."""
    found = stubs(ctx.caller)
    single = {}
    for stub in found:
        single[stub[2]] = single.get(stub[2], 0) + 1
    legs = []
    for position, stub in enumerate(found):
        workflow = stub[2]
        given = inputs(ctx, workflow, stub[3])
        if workflow in SINGLE:
            fixed, job = SINGLE[workflow], base_name(stub[1])
            name = fixed if single[workflow] == 1 or job == fixed else f"{fixed}-{job}"
            builder = {"actionlint": actionlint_leg, "clang-format": format_leg, "coverage": coverage_leg,
                       "consumption": consumption_leg}[workflow]
            legs.append(builder(ctx, stub, given, name, position))
        elif workflow in SUITES:
            legs += suite_legs(ctx, stub, given, SUITES[workflow], position)
        elif workflow == "clang-tidy":
            legs += tidy_legs(ctx, stub, given, position)
        elif workflow == "sanitizers":
            legs += sanitizer_legs(ctx, stub, given, position)
        else:
            legs.append(skipped_leg(stub, position))
    if ctx.system():
        for step in (step for leg in legs for step in leg.steps):
            step.env = {key: value for key, value in step.env.items() if not key.startswith("VCPKG_")}
    return sorted(unique(legs), key=lambda leg: leg.order)


def select(legs, patterns, stable, runnable_only=True):
    """The legs the patterns name, in the order given, or every runnable one; --stable keeps the stable and tier-free."""
    if stable:
        legs = [leg for leg in legs if leg.tier in (None, "stable")]
    if not patterns:
        return [leg for leg in legs if not (runnable_only and leg.skip)]
    chosen = []
    for pattern in patterns:
        matches = [leg for leg in legs if fnmatch.fnmatchcase(leg.name, pattern)]
        if not matches:
            raise SystemExit(f"no leg matches '{pattern}'; --list names them")
        chosen += [leg for leg in matches if all(leg is not other for other in chosen)]
    return chosen


def pins(ctx):
    """The cpp-ci commits the caller's stubs pin, and this checkout's, for the listing's header."""
    pinned = sorted({stub[4][:7] for stub in stubs(ctx.caller)})
    head = subprocess.run(["git", "-C", str(ctx.cpp_ci), "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                          check=False)
    return pinned, head.stdout.strip() if head.returncode == 0 else "unknown"


def deps_line(ctx):
    """Where the legs' dependencies come from, for the listing and the dry run."""
    if ctx.system():
        return f"dependencies: --deps system, installed in {ctx.boost_prefix}"
    return f"dependencies: --deps vcpkg, from {ctx.vcpkg_root}"


def listing(ctx, legs):
    """--list: every runnable leg in run order with what it runs, then the skipped ones with their reason."""
    width = max([19, *(len(leg.name) for leg in legs)])
    pinned, head = pins(ctx)
    lines = [f"caller {ctx.caller}, pinning cpp-ci {', '.join(pinned) or 'nowhere'}; this checkout is {head}",
             f"legs build under {ctx.out}", deps_line(ctx), "", "run, in order:"]
    lines += [f"  {leg.name:<{width}}  {leg.what}" for leg in legs if not leg.skip]
    lines += ["", "skipped:"]
    lines += [f"  {leg.name:<{width}}  {leg.what}: {leg.skip}" for leg in legs if leg.skip]
    return "\n".join(lines)


def environment(ctx, step):
    """The environment a step runs in: this one, with ~/.local/bin first, vcpkg's root, and the step's own."""
    env = dict(os.environ)
    env["PATH"] = f"{LOCAL_BIN}{os.pathsep}{env.get('PATH', '')}"
    env["VCPKG_ROOT"] = str(ctx.vcpkg_root)
    return env | step.env


def shown(step):
    """A step as a shell line: its directory, its environment and its command."""
    prefix = f"(cd {shlex.quote(str(step.cwd))} && " if step.cwd else ""
    assignments = "".join(f"{key}={shlex.quote(value)} " for key, value in step.env.items())
    return f"{prefix}{assignments}{shlex.join(step.argv)}{')' if step.cwd else ''}"


def run_leg(ctx, leg, log):
    """A leg's steps in order into its log, stopping at the first that fails; whether every step passed."""
    with log.open("w") as stream:
        for step in leg.steps:
            stream.write(f"+ {shown(step)}\n")
            stream.flush()
            try:
                status = subprocess.run(step.argv, env=environment(ctx, step), cwd=step.cwd, stdout=stream,
                                        stderr=subprocess.STDOUT, check=False).returncode
            except FileNotFoundError as error:
                stream.write(f"error: {error}\n")
                status = 127
            if status != 0:
                stream.write(f"error: exit status {status}\n")
                return False
    return True


def run(ctx, legs, keep_going):
    """Runs the legs, then prints one line per leg; the exit status, non-zero when any failed."""
    ctx.out.mkdir(parents=True, exist_ok=True)
    width = max([19, *(len(leg.name) for leg in legs)])
    results, failed = [], False
    for leg in legs:
        if leg.skip:
            results.append(f"{'skip':<4} {leg.name:<{width}} {leg.skip}")
            continue
        if failed and not keep_going:
            results.append(f"{'skip':<4} {leg.name}")
            continue
        print(f"== {leg.name}", flush=True)
        log = ctx.out / f"{leg.name}.log"
        start = time.monotonic()
        verdict = "pass" if run_leg(ctx, leg, log) else "FAIL"
        failed |= verdict == "FAIL"
        results.append(f"{verdict:<4} {leg.name:<{width}} {round(time.monotonic() - start):>5}s  {log}")
    print("\n".join(results))
    return 1 if failed else 0


def deps_mode(flag, environ=os.environ):
    """--deps where given, else CI_LOCAL_DEPS, else vcpkg."""
    mode = flag or environ.get("CI_LOCAL_DEPS") or "vcpkg"
    if mode not in DEPS:
        raise SystemExit(f"CI_LOCAL_DEPS is {' or '.join(DEPS)}, not '{mode}'")
    return mode


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a caller's Linux CI legs locally, from its own workflow stubs.")
    parser.add_argument("--caller", type=Path, default=Path.cwd(), help="the caller's checkout (default: .)")
    parser.add_argument("--out", type=Path, help="where legs build and log (default: <caller>/build/ci-local)")
    parser.add_argument("--deps", choices=DEPS, help="where vcpkg.json's dependencies come from: vcpkg builds them, "
                        "system takes what is installed (default: CI_LOCAL_DEPS, else vcpkg)")
    parser.add_argument("--list", action="store_true", help="list the legs in run order, and the skipped ones, and exit")
    parser.add_argument("--dry-run", action="store_true", help="print each selected leg's commands instead of running them")
    parser.add_argument("-k", "--keep-going", action="store_true", help="run every selected leg, whatever fails")
    parser.add_argument("--stable", action="store_true", help="keep only the stable tier's legs and the tier-free ones")
    parser.add_argument("legs", nargs="*", metavar="LEG_OR_GLOB", help="legs to run, by name or glob (default: all)")
    args = parser.parse_args(argv)

    caller = args.caller.resolve()
    if not (caller / ".github/workflows").is_dir():
        raise SystemExit(f"{caller} has no .github/workflows: pass the caller's checkout with --caller")
    ctx = Context(caller, (args.out or caller / "build/ci-local").resolve(), deps=deps_mode(args.deps))
    legs = discover(ctx)
    if args.list:
        print(listing(ctx, select(legs, args.legs, args.stable, runnable_only=False)))
        return 0
    chosen = select(legs, args.legs, args.stable)
    if args.dry_run:
        print(f"# {deps_line(ctx)}")
        for leg in chosen:
            print(f"== {leg.name}" + (f"  (skipped: {leg.skip})" if leg.skip else ""))
            print("\n".join(shown(step) for step in leg.steps))
        return 0
    if (caller / "vcpkg.json").is_file():
        if ctx.system() and not any(ctx.boost_prefix.glob("lib/cmake/Boost-*/BoostConfig.cmake")):
            print(f"warning: no Boost in {ctx.boost_prefix}: run tools/setup-toolchain.sh --boost, or set BOOST_PREFIX",
                  file=sys.stderr)
        elif not ctx.system() and not Path(ctx.toolchain_file()).is_file():
            print(f"warning: no vcpkg in {ctx.vcpkg_root}: run tools/setup-toolchain.sh, or set VCPKG_ROOT", file=sys.stderr)
    return run(ctx, chosen, args.keep_going)


if __name__ == "__main__":
    sys.exit(main())
