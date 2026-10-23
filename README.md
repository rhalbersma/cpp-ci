# Reusable Continuous Integration for C++ projects

[![Project Status: Active](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active)
[![License](https://img.shields.io/badge/license-Boost-blue.svg)](https://opensource.org/licenses/BSL-1.0)
[![Actionlint](https://github.com/rhalbersma/cpp-ci/actions/workflows/actionlint.yml/badge.svg)](https://github.com/rhalbersma/cpp-ci/actions/workflows/actionlint.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/rhalbersma/cpp-ci/badge)](https://scorecard.dev/viewer/?uri=github.com/rhalbersma/cpp-ci)

Shared GitHub Actions workflows for C++ libraries built with CMake, whose
dependencies come from a `vcpkg.json` manifest and whose tests are registered
with CTest. Calling repositories keep a thin stub per workflow and no CI logic
of their own.

CMake is the hard requirement, and it goes deeper than driving the build:
`cxx_flags` reaches the compiler as `CMAKE_CXX_FLAGS`, the Release and Debug
legs are `CMAKE_BUILD_TYPE`, `clang-tidy.yml` needs the compile database
`CMAKE_EXPORT_COMPILE_COMMANDS` writes, and the three consumption models are
CMake's own.

Two things are the caller's to choose, and this side is indifferent to both.

The **shape of the library** — header-only, or headers with compiled sources.
A leg configures, builds and tests whatever the CMake project defines, and
[static analysis](#static-analysis) reads the library's own sources from the
compile database, so compiled sources need no configuration that a header-only
library does not.

The **test framework** — Boost.Test, GoogleTest, Catch2. A leg runs
`vcpkg install` in manifest mode and then `ctest`, so the framework is a line
in the caller's `vcpkg.json` and its own CMake; swapping one for another
changes nothing here.

The term is meant in the broad sense: not unit testing alone, but every check
a change should survive before it merges.

| Kind of check | What it establishes | Workflows |
| :------------ | :------------------ | :-------- |
| [Unit testing](#unit-testing) | the library compiles, and its tests pass, on every rung named | the six ladders |
| [Coverage](#coverage) | every line and branch is exercised | `coverage.yml` |
| [Sanitizers](#sanitizers) | it works with no undefined behaviour, bad access or leak | `sanitizers.yml` |
| [Static analysis](#static-analysis) | no finding from a linter or a security query suite | `clang-tidy.yml`, `msvc-analyze.yml`, `codeql.yml` |
| [Consumability](#consumability) | the installed package can actually be consumed | `consumption.yml` |
| [Code formatting](#code-formatting) | the tree matches the caller's `.clang-format` | `clang-format.yml` |
| [Workflow checks](#workflow-checks) | the Actions files are valid and the supply chain pinned | `actionlint.yml`, `scorecard.yml` |

Those seven are in descending order of what a green check tells you about the
library, and the groups are the point: the first two are the test suite and
whether it is worth trusting, the next two are defects the tests do not assert
on — at runtime, then without running — and the last three are the package, the
source text, and this CI rather than the library at all.

The rest of this file is in the order a caller needs it.
[Dependencies](#dependencies) is what a caller has to declare and where,
[Tiers](#tiers-not-versions) is what a rung resolves to,
[Usage](#usage) is what a stub contains, [Workflows](#workflows) is what each
one runs and what its gate is called, [Actions](#actions) and
[Conventions](#conventions) are the pieces underneath, and
[Standard library test suites](#standard-library-test-suites) is a CMake
module a caller's test tree can include. Each section opens with
a table and spells out below it only what a table cannot carry.

## Dependencies

Third-party dependencies come from a `vcpkg.json` manifest; one with no vcpkg
port arrives through CMake instead.

| Source | How the caller declares it | Examples |
| :----- | :------------------------- | :------- |
| A vcpkg port | `vcpkg.json`, test-only ones behind a feature | `boost-test`, `boost-hash2`, `fmt`, `benchmark`, `range-v3` |
| No port, typically a sibling library | `find_package(... CONFIG QUIET)`, with a `FetchContent` fallback | `xstd-ints`, `xstd-misc` |

A manifest is required either way: every leg that configures the library runs
`vcpkg install` in manifest mode, unconditionally and with no guard for a
missing `vcpkg.json`. A library with nothing much to declare still needs the
file.

Put the test-only dependencies behind a feature, so a tests-off configure does
not need them. There are two ways to have that feature installed here, and they
differ in what a *consumer* of the package resolves rather than in what CI does:

| The manifest says | The caller passes | A consumer resolving the manifest gets |
| :---------------- | :---------------- | :------------------------------------- |
| `default-features` names the feature | nothing | the test dependencies too |
| no default features | `vcpkg_features: <name>` | nothing |

The second is what a header-only library with no dependency of its own wants:
`default-features` naming a `test` feature means an ordinary manifest-mode
install resolves Boost for someone who only ever includes a header. The input
is comma-separated and reaches both halves of a leg — the `vcpkg install` step
and `VCPKG_MANIFEST_FEATURES` on the configure line — so the binary cache is
warmed for what the build then asks for. It is additive either way: the default
features install regardless, so a caller naming nothing is unaffected.

Either way [`consumption.yml`](#consumability) defaults `vcpkg: false` — it
configures with `-DBUILD_TESTING=OFF`, which normally leaves a header-only
library needing nothing at all, and it takes no `vcpkg_features` for that same
reason. A library whose own headers `find_package(... REQUIRED)` something
passes `vcpkg: true`.

The `FetchContent` fallback needs care on exactly one leg. `install(EXPORT)`
cannot export a FetchContent build tree, so the `find_package` consumption
model needs that dependency really installed rather than fetched; the
`dependency_repos` input does that, at pinned revisions. Everywhere else the
fallback is invisible.

## Tiers, not versions

Every toolchain is tracked on three rungs — **stable**, **qualification** and
**development** — and this table is what each rung resolves to, for every
family the resolver knows.

| Family | Platform | Standard library | Stable | Qualification | Development |
| :----- | :------- | :--------------- | :----- | :------------ | :---------- |
| `gcc` | Linux, `ubuntu-24.04` | libstdc++ | 15 | 16 | 17-SVN |
| `mingw` | Windows, `windows-2022` | libstdc++ | 15 | 16 | — |
| `clang` | Linux, `ubuntu-24.04` | libstdc++ | 22 (libstdc++ 15) | 23 (libstdc++ 16) | 24-SVN (libstdc++ 17-SVN) |
| `clang` | Linux, `ubuntu-24.04` | libc++ | 22 | 23 | 24-SVN |
| `apple-clang` | macOS | libc++ | Xcode 16.4, `macos-15` | Xcode 26.6, `macos-26` | — |
| `msvc` | Windows | MSVC STL | 2022, `windows-2022` | 2026, `windows-2025` | 2026-Preview, `windows-2025` |

Callers name a rung, never a version. The rungs live in
[`toolchains.json`](.github/actions/toolchain/toolchains.json), so a compiler
release is one edit here rather than one per repository, and every caller moves
up on its next pin bump.

The ladder's scope is the three most recent releases of each family that a
current GitHub Actions image can run — not the full range a library supports.
The older compilers a header-only library often still compiles with are absent
because the images that carried them are gone, and a runner is the only thing
this CI has to offer. So the ladder is the recent end of a library's support
range by construction, and the rest of that range is the caller's to record;
see [loosening and tightening](#loosening-and-tightening-the-ladder).

The two `clang` rows are one ladder, not two: the same release, differing in
`-stdlib=`, which is why `clang.yml` and `clang-libc++.yml` take the same rung
names. A caller's **Clang-CL** row is the `msvc` ladder too — the same rungs
with `-T ClangCL`, per [`visual-studio.yml`](#visual-studioyml) — so a change
to the MSVC rungs moves both. The clang-cl release itself is whichever one the
selected Visual Studio bundles: a runner-image fact, not something this
repository pins, and the one number in a caller's matrix that this table cannot
promise to keep current.

The runner images are not uniform across the Windows rows. The `msvc` rungs are
tied to theirs: Visual Studio 18 ships on `windows-2025` and VS 17 on
`windows-2022`, so each rung sits on the image carrying its toolset. `mingw`
has no such tie — a WinLibs toolchain is a self-contained archive this CI
downloads, and the image supplies only pwsh, the preinstalled vcpkg and Ninja,
which both images carry — so its image is a free choice, and it sits on
`windows-2022`. Both images are under test either way: `windows-2022` carries
`mingw` and the stable `msvc` and Clang-CL rungs, `windows-2025` the other two
MSVC rungs.

Two rungs are empty, and the resolver reports `supported=false` for one rather
than inventing a compiler: **`apple-clang` development**, because Apple
publishes no Clang trunk, and **`mingw` development**, because WinLibs
publishes no snapshot newer than its own releases and none for GCC 17. Callers
passing the default `stable,qualification,development` are unaffected — an
absent rung is dropped with a notice. An empty rung is a fact about the vendor;
a caller *dropping* a rung the vendor does fill is a fact about that library,
and belongs in its README rather than here.

## Usage

A caller keeps one stub per workflow, and this is the division of labour
between the two files.

| Lives in the caller's stub | Lives in the shared workflow |
| :------------------------- | :--------------------------- |
| the `on:` triggers | resolving the requested rungs to a matrix |
| the `concurrency:` group | installing the toolchain and the dependencies |
| `permissions:` | configure, build and `ctest` on every leg |
| the job id, which prefixes every check name | the `all` gate that reports one stable name |
| the `with:` inputs, where the defaults do not fit | everything else |

The triggers are the reason the stub exists at all: a reusable workflow cannot
carry its own. The job id matters because it prefixes the status check name, so
a job `gcc` calling a workflow whose own job is named `15 Debug` appears as
`gcc / 15 Debug`.

```yaml
name: GCC

permissions:
  contents: read

# Concurrency lives here, not in the shared workflow: only the stub knows
# whether it was reached by a push, a schedule, or an aggregating canary.
concurrency:
  group: ${{ github.workflow_ref }}-${{ github.ref }}
  cancel-in-progress: true

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:
  # Only when an aggregating workflow calls this stub in turn.
  workflow_call:

jobs:
  gcc:
    uses: rhalbersma/cpp-ci/.github/workflows/gcc.yml@<sha> # <version>
```

A repository that cannot build on every rung names the ones it can. The
platform workflows take one list of rungs; `sanitizers.yml` takes one per
compiler family, since its legs are split between them:

```yaml
  # gcc.yml, clang.yml, clang-libc++.yml
    with:
      tiers: qualification,development
      cxx_flags: -Wno-interference-size

  # sanitizers.yml
    with:
      gcc_tiers: qualification,development
      clang_tiers: development
      # Empty drops the ASan libc++ leg, for a library libc++ cannot build.
      libcxx_tiers: ""
```

Two inputs set how much a build writes rather than what it checks, and default
to the lean side wherever they are taken, so a caller gets them by bumping its
pin and opts back one stub at a time.

| Input | Default | Passed as | Taken by |
| :---- | :------ | :-------- | :------- |
| `scan_for_modules` | `false` | `CMAKE_CXX_SCAN_FOR_MODULES` | every workflow that configures and builds; `clang-tidy` keeps it off, fixed |
| `debug_flags` | `-g1` | `CMAKE_CXX_FLAGS_DEBUG`; empty passes nothing | `gcc`, `clang`, `clang-libc++`, `apple-clang`, `mingw`, `sanitizers`, `coverage`, `codeql` |

CMake 3.28 and later scans every translation unit of a C++20 target for the
modules it imports, which for a library importing none is a preprocessor pass
per unit and a fully preprocessed copy of each left in the build tree — about
740 MB for 153 units in one caller. `-g1` keeps the line tables and function
names a symbolized sanitizer report needs, and `gcov` reads no debug
information at all, so `sanitizers.yml` and `coverage.yml` set no `-g` of their
own and take this one. The MSVC workflows take no `debug_flags`, since their
debug options are `/Zi`-style, and neither does `consumption.yml`, which builds
no Debug configuration. Both are passed ahead of `cmake_args`, so a `-D` there
still wins.

## Workflows

Eighteen workflows, and the ten that compile and run the caller's test suite
are the ones that say most; this table sorts all of them by what each does with
that suite.

| The caller's test suite | Workflows |
| :---------------------- | :-------- |
| Configured, built and run under `ctest` | the six ladder workflows, `emscripten.yml`, `s390x.yml`, `sanitizers.yml`, `coverage.yml` |
| Configured and built, not run | `codeql.yml`, `msvc-analyze.yml` |
| Configured off; a consumer built instead | `consumption.yml` |
| Configured for a compile database only | `clang-tidy.yml` |
| Not configured at all | `clang-format.yml`, `actionlint.yml`, `scorecard.yml` |

`Gate`, in the tables below, is the check name to tick under branch
protection, with `<job>` standing for the caller's own job id: a stub whose job
is `clang_libcxx` reports `clang_libcxx / all`.

### Unit testing

Seven compiler rows over six workflows — Clang-CL and MSVC share one — each
building and running the caller's test suite on every rung it names.

| Compiler | Standard library | Workflow | Default rungs | Legs per rung | Gate |
| :------- | :--------------- | :------- | :------------ | :------------ | :--- |
| GCC | libstdc++ | [`gcc.yml`](.github/workflows/gcc.yml) | all three | Release, Debug | `<job> / all` |
| MinGW | libstdc++ | [`mingw.yml`](.github/workflows/mingw.yml) | the two it fills | Release, Debug | `<job> / all` |
| Clang | libstdc++ | [`clang.yml`](.github/workflows/clang.yml) | all three | Release, Debug | `<job> / all` |
| Clang | libc++ | [`clang-libc++.yml`](.github/workflows/clang-libc++.yml) | all three | Release, Debug | `<job> / all` |
| Apple Clang | libc++ | [`apple-clang.yml`](.github/workflows/apple-clang.yml) | the two it fills | Release, Debug | `<job> / all` |
| Clang-CL | MSVC STL | [`visual-studio.yml`](#visual-studioyml), `-T ClangCL` | all three | Release, Debug | `<job> / all` |
| MSVC | MSVC STL | [`visual-studio.yml`](#visual-studioyml) | all three | Release, Debug | `<job> / all` |

Every leg configures the caller's repository, builds it, and runs its test
suite under `ctest`, so a green gate here means the library compiles *and*
passes on every rung named, in both build types.

They share a shape: a first job resolves the requested rungs to a strategy
matrix, a second builds and tests them, and a third gate reports one check name
that does not move as the ladder does. The legs are named for compiler versions
— `15 Debug`, `24-SVN Release` — and those names slide forward as the ladder
does, which is why the gate a caller requires is `all` rather than any leg.

Every rung runs on every event, **pull requests included**. `reduce_on_pr` opts
a caller into running the floor and the ceiling in Debug alone on a pull
request — the oldest compiler the code claims and the one that changes weekly —
and defaults to `false` on every workflow that takes it. The cost of `true` is
in the gate: it reports on the legs that ran, so a reduced pull request can go
green having never compiled the middle rung, and a break there merges before
the push that finds it. Pass it only where something else genuinely covers that
rung.

**Concurrency belongs to the caller.** None of these declare a concurrency
group: inside a called workflow `github.workflow_ref` names the *calling*
workflow, so a group defined here would land in the caller's own group and
cancel it. The stub knows whether it was reached by a push, a schedule, or a
canary; these do not.

**The Linux rows size their build by memory,** as the
[sanitizer build](#sanitizers) does. `gcc.yml`, `clang.yml` and
`clang-libc++.yml` take `build_memory_mib`, 4096 by default, and run as many
compile jobs as `MemAvailable` holds at that budget, at least one and at most
one per CPU, where Ninja alone would run two more than the CPUs. A
template-heavy suite's heaviest units peak at several GiB, more with libc++,
and three of them coinciding on a 16 GB runner leave it shut down mid-Build
rather than reporting a compiler error. A caller whose units are small passes
less.

#### 32-bit targets

Every row above builds for a 64-bit target, so none of them can see code that
takes `size_t` to be 64 bits, `alignof(std::int64_t)` to be 8, or `__int128`
to exist. Three legs build for targets where those fail, and they fail
differently, so each catches what the others cannot:

| Target | Workflow | `size_t` | `alignof(std::int64_t)` in a class | `__int128` | Tests run under |
| :----- | :------- | :------- | :--------------------------------- | :--------- | :-------------- |
| i686 | [`gcc.yml`](.github/workflows/gcc.yml) with `arch: x86` | `unsigned int` | 4 | no | the runner, natively |
| x86 | [`visual-studio.yml`](#visual-studioyml) with `arch: x86` | `unsigned int` | 8 | no | the runner, natively |
| wasm32 | [`emscripten.yml`](.github/workflows/emscripten.yml) | `unsigned long` | 8 | yes | node |

`arch: x86` adds `-m32` and installs the GCC's multilib, with the dependencies
from the `x86-linux-gcc` overlay triplet. The trunk snapshot has no 32-bit
runtime, so an i686 ladder names released rungs only. `visual-studio.yml`
takes the same input: `arch: x86` configures with `-A Win32`, keeping the
x64-hosted tools, against vcpkg's built-in `x86-windows` triplet. Its leg is
the one where 64-bit members keep 8-byte alignment in a 32-bit class, and
where the MSVC STL's 128-bit integer class takes its portable path rather than
the x64 intrinsics. `emscripten.yml` takes one Emscripten release,
`emsdk_version`, rather than a ladder, and builds in Debug by default; its
dependencies come from `wasm32-emscripten-eh`, vcpkg's community triplet with
exceptions compiled in. Its gate is `<job> / all`.

```yaml
  i686:
    uses: rhalbersma/cpp-ci/.github/workflows/gcc.yml@<sha> # <version>
    with:
      arch: x86
      tiers: stable
      build_types: Debug

  msvc_x86:
    uses: rhalbersma/cpp-ci/.github/workflows/visual-studio.yml@<sha> # <version>
    with:
      arch: x86
      tiers: qualification
      build_types: Debug

  wasm32:
    uses: rhalbersma/cpp-ci/.github/workflows/emscripten.yml@<sha> # <version>
```

#### Big-endian target

Every other leg builds for a little-endian target, so none of them can see code
that takes the first byte of an integer to be its lowest. One leg builds for a
big-endian one:

| Target | Workflow | Runner | Compiler | Tests run under |
| :----- | :------- | :----- | :------- | :-------------- |
| s390x | [`s390x.yml`](.github/workflows/s390x.yml) | `ubuntu-26.04` | Ubuntu's `g++-15-s390x-linux-gnu` | `qemu-s390x`, user-mode |

s390x because it is a current platform — IBM's z/Architecture, an official
architecture of Ubuntu, RHEL and SLES — where big-endian `powerpc64` Linux has
been dropped for `ppc64le`. It sits on `ubuntu-26.04` because the newest s390x
cross GCC `ubuntu-24.04` packages is 14, below the stable rung; on 24.04 that
package's install also removes `gcc-multilib`, so it could not share a runner
with the i686 leg in any case. The dependencies come from `s390x-linux-gcc`, an
overlay triplet building them with the same cross GCC, and its toolchain file is
chainloaded for the caller's project too, so `ctest` runs every test under the
emulator with no binfmt registration. The dependencies' include directories
reach the compiler as `-isystem`, as on a native leg, so their own warnings do
not fail a `-Werror` build.

Like `emscripten.yml` it takes one release, `gcc_version`, rather than a
ladder, and builds in Debug by default; its gate is `<job> / all`. Emulation
makes the test suite several times slower than on a native leg, so a caller
stubs it with `workflow_dispatch` alone and has its nightly canary dispatch it,
rather than running it on pull requests.

```yaml
  # s390x.yml -- dispatched by the canary, never on a pull request.
  s390x:
    uses: rhalbersma/cpp-ci/.github/workflows/s390x.yml@<sha> # <version>
```

#### Mobile targets

Two workflows build for the phone platforms, arm64 only, with one leg per
supported release of the operating system:

| Target | Workflow | Legs | Toolchain | Tests run under |
| :----- | :------- | :--- | :-------- | :-------------- |
| Android | [`android-ndk.yml`](.github/workflows/android-ndk.yml) | API 35, 36 and 37: Android 15, 16 and 17 | NDK r30: Android Clang 21 and its libc++ | `qemu-aarch64`, user-mode |
| iOS | [`ios.yml`](.github/workflows/ios.yml) | iOS 18, 26 and 27 | Xcode 26.3 on `macos-15`, 26.6 on `macos-26`, 27 on `xcode-27` | the iOS Simulator of the leg's release |

What they check is that the caller's language and library use builds and passes
its tests on each platform's toolchain and standard library, at each release's
level. Packaging, signing and running on a device are an app's concern, not a
library's.

**Android.** One NDK serves every API level, as its sysroot carries each
level's bionic headers and startup objects, so the legs differ in what they are
compiled against: a call the level lacks fails there. Executables link bionic
and libc++ statically and run on the runner's kernel, with no Android loader or
system image, so the tests exercise the code on arm64 bionic rather than on a
device's release of Android. The dependencies come from `arm64-android-ndk`,
an overlay triplet building them with the same NDK at the same level, and its
toolchain file, which takes CMake's own Android support over the NDK's legacy
one, is chainloaded for the caller's project too.

**iOS.** The deployment target turns libc++'s availability markup on, so a
library call the oldest supported release lacks fails to compile, and the
simulator runtime carries that release's own libc++ dylib, so one it ships
wrongly fails its test. Each image ships the simulator runtimes of its own
Xcodes only, so a leg runs on the image that has its release and builds with
the newest Xcode there: an app supporting iOS 18 builds with a current Xcode
at an 18.0 deployment target, not with the Xcode that first shipped the iOS 18
SDK. The dependencies come from `arm64-ios-simulator-xcode`, built with the
same Xcode at the same deployment target.

Both take Debug by default. The Android leg, like the s390x one, defaults
`debug_flags` to `-g1 -O1`: unoptimized code is the emulator's worst case, and
one Boost.Hash2 test of xstd-misc takes 274 s under it at `-O0` against 1 s at
`-O1`. Each workflow's gate is `<job> / all`.

```yaml
  android:
    uses: rhalbersma/cpp-ci/.github/workflows/android-ndk.yml@<sha> # <version>

  ios:
    uses: rhalbersma/cpp-ci/.github/workflows/ios.yml@<sha> # <version>
```

#### `visual-studio.yml`

One workflow stubbed twice, once per toolset, which is what a caller's README
shows as its separate `MSVC` and `Clang-CL` rows. A third stub, for the
[32-bit](#32-bit-targets) leg, changes only the target.

| Stub job | `toolset` | `preview_toolset_prefix` | `cache_prefix` | `arch` |
| :------- | :-------- | :----------------------- | :------------- | :----- |
| `msvc` | default, `host=x64` | default, empty | default, `msvc` | default, `x64` |
| `clang_cl` | `ClangCL,host=x64` | `"ClangCL,"` | `clang-cl` | default, `x64` |
| `msvc_x86` | default, `host=x64` | default, empty | default, `msvc` | `x86` |

Named for the toolchain rather than the compiler: MSVC's `cl` and the
`clang-cl` each Visual Studio bundles are the same build in every respect but
the `-T` argument, so they are one workflow with two callers rather than two
files that would drift.

```yaml
  # msvc.yml -- no inputs; the defaults select the MSVC toolset.
  msvc:
    uses: rhalbersma/cpp-ci/.github/workflows/visual-studio.yml@<sha> # <version>

  # clang-cl.yml -- the same ladder, clang-cl in front of the MSVC STL.
  clang_cl:
    uses: rhalbersma/cpp-ci/.github/workflows/visual-studio.yml@<sha> # <version>
    with:
      toolset: ClangCL,host=x64
      preview_toolset_prefix: "ClangCL,"
      # Its own cache: the two callers build the dependencies with different
      # compilers.
      cache_prefix: clang-cl
```

The two toolsets need separate caches because they build the dependencies with
different compilers; the vcpkg cache key carries `arch` itself, so `msvc_x86`
shares `msvc`'s prefix without evicting its archives. `arch: x86` ignores
`vcpkg_triplet` and installs for `x86-windows`, as `gcc.yml`'s ignores it for
`x86-linux-gcc`. The development rung here is a **preview channel of the
same Visual Studio generation**, not a newer one, which is why the `msvc`
ladder's qualification and development rungs share a label and a runner.
`preview_toolset_prefix` is separate from `toolset` because that rung takes a
`version=<n>` the released ones do not.

### Coverage

One job on one rung, running the test suite under `ctest` and then `gcovr`,
with the bar as a hard gate rather than a report.

| Measure | Default bar | Enforced by |
| :------ | :---------- | :---------- |
| Line coverage | `--fail-under-line 100` | [`coverage.yml`](.github/workflows/coverage.yml), gate `<job> / gcovr` |
| Branch coverage | `--fail-under-branch 100` | the same job |
| Project and patch | the caller's own `codecov.yml` | Codecov's `codecov/project` and `codecov/patch` |

Both thresholds are inputs, so a caller ratcheting up from below 100 passes
lower numbers rather than turning the gate off. `exclude` defaults to
`test/.* build/.*`, keeping the report to the library's own headers.

A line that begins with `assert(`, or with a project's own assert macro, an
upper-case name ending in `_ASSERT` such as `XSTD_BITS_ASSERT(`, is excluded from
both counts: its false arm is the one a passing suite never takes. An assert
written anywhere else on its line is counted.

It sits on the stable `gcc` rung because what it measures is the code rather
than the toolchain, and because `gcov` has to match the compiler that produced
the data. Codecov's two checks, where a caller enables them, are that caller's
configuration and not this repository's.

### Sanitizers

Five Linux legs, each building and running the caller's test suite in Debug on
every rung its compiler fills — fifteen jobs by default, on every event.

| Leg | Compiler | Flags |
| :-- | :------- | :---- |
| ASan + LSan | GCC | `-fsanitize=address -fno-omit-frame-pointer` |
| ASan + LSan | Clang, libc++ | the same, plus `-stdlib=libc++` |
| UBSan | GCC | `-fsanitize=undefined -fno-sanitize-recover=undefined` |
| UBSan | Clang | `-fsanitize=undefined -fno-sanitize-recover=undefined` |
| Implicit conversion | Clang | `-fsanitize=implicit-conversion -fno-sanitize-recover=implicit-conversion` |

The build is sized by memory rather than by CPUs. The heaviest translation
units of a template-heavy suite peak at several GiB, more with libc++ than with
libstdc++, and Ninja's default of two jobs more than the CPUs lets several
coincide on a 16 GB runner, which then stops answering mid-Build instead of
reporting a compiler error.
`build_memory_mib`, 4096 by default, is the budget per compile: the build runs
as many jobs as `MemAvailable` holds at that budget, at least one and at most
one per CPU — three on today's hosted runner — and reports the count as a
notice on the run. A caller whose units are small passes less.

The legs cross the ladder rather than pinning one release, because a
sanitizer's instrumentation is no more fixed across releases than across
compilers: what a run reports depends on the toolchain doing the
instrumenting. It takes one rung list per compiler family, since the legs are
split between them — `gcc_tiers`, `clang_tiers`, and `libcxx_tiers` for the
ASan libc++ leg — and the gate is `<job> / all`.

ASan runs against both standard libraries. Its detection is a shared runtime,
so a second compiler alone would re-run one check — but the container-overflow
annotations are not shared: libc++ instruments `vector`, `string` and `deque`,
while libstdc++ annotates `vector` alone and only under
`_GLIBCXX_SANITIZE_VECTOR`. An overflow inside a `std::string` is invisible to
the first leg and visible to the second. That leg rebuilds the manifest's
dependencies against libc++ through an overlay triplet, since a test framework
built against the other standard library would not link with it in any case.

Leak detection is **on**: `ASAN_OPTIONS=detect_leaks=1` is set explicitly, so a
repository that allocates gets the check rather than inheriting a suppression
written for one that does not.

The libc++ leg has its own `libcxx_tiers`, defaulting to the Clang ladder. A
library can be perfectly sound and still not build against libc++ at all — one
missing range adaptor is enough — and that is a fact about the standard library
rather than about the sanitizer. Left in the Clang ladder, such a library
reddens this workflow's gate permanently, which is worse than not running the
leg: a gate that is always red reports nothing about the legs that were meant
to be green. `libcxx_tiers: ""` drops it.

UBSan runs under both compilers because the two implementations do not check
the same set, so a clean run under one says nothing about the other.
`-fsanitize=implicit-conversion` is Clang's alone (`g++` rejects the group) and
catches the value-dependent truncations and sign changes that `-Wconversion`
can only diagnose where it proves them statically. That group also fires on
well-defined but lossy conversions, which a standard library is full of by
design, so it runs with an ignorelist confining it to the code under test;
without one it reports only on libstdc++.

Deliberately absent: **TSan** (no threads), **MSan** (needs an instrumented
libstdc++ *and* an instrumented test framework),
**`-fsanitize=unsigned-integer-overflow`** (the wraparound is deliberate),
**`_GLIBCXX_DEBUG`** (ABI-changing, so the test framework would need rebuilding
to match), and **CFI** (no virtual dispatch). Linux-only
by necessity: MSVC offers ASan alone, macOS has no LeakSanitizer, MinGW no
usable runtime.

### Static analysis

Three analyzers, one workflow each, and the two bound to a toolset run the
whole ladder rather than one rung.

| Workflow | Analyzer | Default rungs | Gate |
| :------- | :------- | :------------ | :--- |
| [`clang-tidy.yml`](.github/workflows/clang-tidy.yml) | the caller's `.clang-tidy`, over the public headers, their per-header translation units and the test sources | all three `clang` | `<job> / all` |
| [`msvc-analyze.yml`](.github/workflows/msvc-analyze.yml) | MSVC's `/analyze`, in the role clang-tidy fills on the other side | all three `msvc` | `<job> / all` |
| [`codeql.yml`](.github/workflows/codeql.yml) | the `c-cpp` `security-extended` query suite | stable `gcc` | `<job> / Analyze` |

A finding fails the run in every case — `WarningsAsErrors` on the clang-tidy
side, `warnings_as_errors: true` by default on the MSVC side — because a check
nobody has to act on stops being a check.

The two toolset analyzers run **all three** rungs for the reason the ladders
do: a toolset gains, renames and retires checks, so it changes the verdict on
code nobody touched. A caller whose compiler legs are green on three rungs is
claiming all three as buildable, and a reader who builds with the newest runs
*its* analyzer over their own translation units. Both take Debug, since
`-DNDEBUG` rewrites every `assert` before the analyzer sees it.

`codeql.yml` sits on stable because what it queries is the code rather than the
toolchain; its extractor runs front-end side, so that rung is also the standard
library the analysis sees. `clang-tidy.yml` configures a compile database and
never builds the library, which is why it appears under that line in the table
above.

`clang-tidy.yml` makes four passes, and three of them are named by a directory
rather than a pattern — the same three a CMake project already has:

| Pass | What it covers | Input | Default |
| :--- | :------------- | :---- | :------ |
| Generated per-header units | the self-sufficiency translation units the caller's CMake generates | `self_sufficiency_regex` | — |
| Direct header dependencies | each header again as a primary input, so `misc-include-cleaner` can tell a direct include from a transitive one | `include_dir`, `include_exclude` | `include` |
| Library sources | the library's own compiled sources | `src_dir` | `src` |
| Test sources | the test tree | `test_dir` | `test` |

Each input is named for the directory it defaults to, which also keeps
`src_dir` clear of CMake's own `CMAKE_SOURCE_DIR` — that one means the project
root, not `src`.

The three directory passes work the same way: the pass runs over whatever the
compile database already holds under that directory. A directory that is not
there prints a line and the pass is skipped, so a header-only library has
nothing to opt out of; a library keeping its sources elsewhere names the
directory; and `""` disables a pass outright.

The first pass is the exception, and has to be. Those units are generated into
the **build** tree, under a path only the caller's own CMake knows, so there is
no convention to discover them by and the pattern stays a caller's to give.
`sources_regex` is kept as an explicit override for the test pass, and
`header_exclude` as an alias for `include_exclude`, so an existing stub keeps
working; neither is needed in a new one.

### Consumability

One job on one rung, building each consumption model the caller ships under
`consumer_dir` (`test/consumer` by default) against the library configured with
`-DBUILD_TESTING=OFF`.

| Model | Subdirectory | Configured against |
| :---- | :----------- | :----------------- |
| `find_package` | `find_package/` | the installed package, via `CMAKE_PREFIX_PATH` |
| `add_subdirectory` | `add_subdirectory/` | the source tree |
| `FetchContent` | `fetch_content/` | the source tree, fetched |

Each is a real CMake project rather than a heredoc, so it configures outside CI
too, and a repository supporting only some of them simply omits the rest: one
documenting two of the three passes, one documenting none fails rather than
reporting success having consumed nothing.

Tests off is normally enough to leave a header-only library with no dependency
at all here, which is why the default configures against no toolchain file and
no prefix path. Two inputs cover the libraries where it is not:

```yaml
    with:
      # The library itself -- not only its tests -- has a
      # find_package(... REQUIRED), so a tests-off configure still needs it,
      # and so does anyone consuming the installed package.
      vcpkg: true
      # Cloned, built and installed into a shared prefix before anything else
      # is configured. One "<url> <revision>" per line; the revision is
      # required, so this leg does not depend on what upstream pushed today.
      dependency_repos: |
        https://github.com/<owner>/<repo>.git <sha>
```

The library is configured against vcpkg's toolchain file, which puts vcpkg in
*manifest* mode because the tree being configured is the caller's repository
root, where its `vcpkg.json` lives. The consumers are not: each is its own
small project with no `vcpkg.json`, so the same toolchain file would put them
in *classic* mode, looking in vcpkg's global `installed/` tree, which a
manifest install never writes to. They get
`<workspace>/vcpkg_installed/<triplet>` on `CMAKE_PREFIX_PATH` instead, and no
toolchain file.

`dependency_repos` exists because `install(EXPORT)` requires every dependency
of an exported target to come from `find_package`: a FetchContent build tree
cannot be exported. A library that finds its dependency with
`find_package(... QUIET)` and falls back to FetchContent therefore configures
fine on the `add_subdirectory` and `fetch_content` models and produces an
invalid package on the `find_package` one — unless that dependency is really
installed first. Each repository is configured with the compiler under test and
with the prefix on its path, so a chain of them resolves in the order given;
none is configured against vcpkg's toolchain file, since manifest mode keys off
the tree being configured and a dependency shipping its own `vcpkg.json` would
install *that* repository's test dependencies here. With `vcpkg: true`, each also
gets `<workspace>/vcpkg_installed/<triplet>` on its path, as the consumers do, so a
dependency that `find_package`s a port finds it, provided the caller's
`vcpkg.json` lists it.

### Code formatting

One job on one rung, checking the tree against the caller's `.clang-format`
without writing to it. The gate is `<job> / clang-format`.

| Input | Default | What it selects |
| :---- | :------ | :-------------- |
| `tier` | `stable` | the Clang release supplying `clang-format` |
| `paths` | `include test` | the roots searched |
| `file_regex` | `.hpp` and `.cpp` | the files checked under them |

`--dry-run --Werror` turns a reformatting diff into a failure rather than a
patch, so the job reports and never rewrites the caller's tree. The default
`file_regex` is a `find` pattern rather than a glob:

```console
.*\.\(hpp\|cpp\)
```

The rung is deliberately its own and lags on stable: a formatter release
reformats the code base, so moving it is a decision about the repository rather
than about coverage, and a caller may well want it to wait. A repository with
no `.clang-format` can leave its stub dispatch-only — adopting a style is a
separate decision from adopting this CI.

### Workflow checks

Two workflows whose subject is the CI itself rather than the library.

| Workflow | What it checks | When it runs | Gate |
| :------- | :------------- | :----------- | :--- |
| [`actionlint.yml`](.github/workflows/actionlint.yml) | Actions syntax and expressions over `.github/workflows/*.yml` | every event | `<job> / actionlint` |
| [`scorecard.yml`](.github/workflows/scorecard.yml) | OpenSSF Scorecard, publishing the result the badge reads | pushes to the default branch, and a schedule | none on a pull request |

actionlint's ShellCheck pass is off unless asked for: `shellcheck` is empty by
default, which keeps the job to Actions syntax and expressions and leaves shell
policy to be managed on its own.

`scorecard.yml` cannot be a required check and is not meant to be — it never
runs on a pull request, so there is nothing for branch protection to wait on.
[`self-check.yml`](.github/workflows/self-check.yml) is this repository's own
and not for callers to stub.

## What a caller still documents

Everything above is this repository's to state; this is where the line falls.

| This repository owns | The caller owns |
| :------------------- | :-------------- |
| what a rung resolves to | which families and rungs it asks for, and why fewer |
| which legs a workflow runs, and in which build types | its badges and its required-check list |
| every input's default | its repo-specific inputs |
| the gate's name | its own floors, and where they sit outside the ladder |

The caller's half is per-repository by nature — badge URLs and
branch-protection settings no shared workflow can carry, and inputs like the
`clang-tidy` regexes, a `cxx_flags` workaround, a `libcxx_tiers: ""` or the
`dependency_repos` a consumption leg needs. Restating this repository's half
instead is what lets several repositories disagree about the same CI.

## Loosening and tightening the ladder

A caller is not bound to the ladder in either direction, and of the three ways
its own requirements can depart from it, CI enforces exactly one.

| The caller's requirement | How it says so | Enforced by a leg |
| :----------------------- | :------------- | :---------------- |
| A baseline *below* the ladder | its README, verified by hand | no |
| A rung it cannot use at all | `tiers:` names the rungs it can | **yes** |
| A floor *inside* a rung | its README, verified by hand | no |

**Looser.** This is the common case, and it is the ladder's shape rather than
any caller's shortfall: the rungs are the recent three, and a library's
language baseline is older than that, with the earliest toolchain that builds
it older still. Those compilers are not on the ladder because GitHub Actions no
longer offers images carrying them, so no leg can be made to cover them at all.
[xstd-misc](https://github.com/rhalbersma/xstd-misc) is the worked example: it
asks for C++20 and names GCC 10, Clang 11 and MSVC 19.29 (VS 2019 16.11) as the
earliest known to compile it, marks them hand-verified, and says plainly that
CI covers only the rungs in its table. That is the right division — a floor no
runner exists for is a claim its author stands behind, not a gate.

**Tighter, by dropping a rung.** Where the library genuinely does not build on
a rung the vendor fills, the caller names the rungs it has and the gate covers
those. [xstd-bits](https://github.com/rhalbersma/xstd-bits) runs no MSVC stable
rung: its `bit_set_view` and `bit_span` are alias templates that the MSVC 17
front end cannot deduce through, while MSVC 18 compiles them clean. Its
Clang-CL row keeps the same VS 2022 rung and passes there, because what it
dropped is one front end and not the image, the STL or the platform — which is
the distinction the [tiers table](#tiers-not-versions) makes when it puts both
on the `msvc` ladder.

**Tighter, inside a rung.** A rung is a Visual Studio generation or a compiler
major, and a caller may need more than its floor.
[xstd-ints](https://github.com/rhalbersma/xstd-ints) writes its MSVC stable
rung as `2022 (17.11+)`: the leg runs whichever 17.x the image carries, and the
annotation records what the library actually needs. Nothing here takes a
minor-version input — `version=<n>` selects the preview toolset build, not a
floor — so a subversion is a documented requirement rather than something a leg
verifies. A caller wanting it enforced has to drop the rung instead.

## Actions

Nine composite actions, which exist because a reusable workflow checks out the
caller rather than this repository.

| Action | Purpose |
| :----- | :------ |
| `toolchain` | Resolve rungs to compilers: one rung, or a whole strategy matrix |
| `apt-retry` | Set the retry key apt actually reads, and drop the image's unused Microsoft source, once per job |
| `install-gcc` | A GCC release from the toolchain PPA, or from the Launchpad librarian when the PPA's mirror is down; or the trunk snapshot |
| `install-clang` | A Clang from apt.llvm.org, optionally with libc++ |
| `install-android-ndk` | One Android NDK release from Google, checked against its published SHA-1, for every API level |
| `vcpkg-setup` | Check out and bootstrap vcpkg at this repository's pinned commit, in place of the runner image's |
| `vcpkg-overlay` | Locate the overlay triplets, which live here rather than in each caller |
| `vcpkg-install` | `vcpkg install`, retried around a download vcpkg will not retry |
| `clang-tidy` | clang-tidy's passes over a configured build, the one script CI and a local run share |
| `gcovr` | The coverage gate's gcovr run and its exclusions, the one script CI and a local run share |

Anything a workflow needs to read from here therefore has to arrive as an
action: an action is fetched from its own repository and `GITHUB_ACTION_PATH`
points at it. That is why the overlay triplets are an action rather than eight
files copied into four repositories, and why the clang-tidy passes and the
coverage gate are scripts behind actions: a developer then runs before pushing
the very scripts CI runs.

## Conventions

References are pinned by commit SHA, with two lookups left unpinned on purpose.

| What | How it is pinned |
| :--- | :--------------- |
| Third-party actions | commit SHA, with a `# vX.Y.Z` comment |
| References to this repository | commit SHA, with a `# vX.Y.Z` comment |
| vcpkg | commit SHA, the `vcpkg-setup` action's default; bumped by hand, since Dependabot does not follow it |
| The WinLibs release lookup | unpinned, by design |
| The GCC trunk `.deb` | unpinned, by design |

Resolve an annotated tag through its peeled ref — `git ls-remote --tags`
otherwise hands back the tag object, which is not a valid pin:

```console
$ git ls-remote --tags https://github.com/rhalbersma/cpp-ci 'v1.0.0^{}'
```

The two unpinned lookups should stay that way: both move by design, and
neither upstream publishes checksums.

## Standard library test suites

libc++, libstdc++ and the MSVC STL each ship a test suite written against the
standard's paragraphs by people who never saw the library under test. A
library that mirrors part of the standard library can run those suites against
itself, rewritten onto its own spelling, as a check whose authors have no stake
in the answer. The adapters live here so that every caller shares one copy of
them.

| Suite | Fetched from | Pinned by | Runnable tests | Markers read | Support headers |
| :---- | :----------- | :-------- | :------------- | :----------- | :-------------- |
| `libcxx` | `llvm/llvm-project` | `clang` stable | `*.pass.cpp`, `*.compile.pass.cpp`, `*.link.pass.cpp` | `REQUIRES`, `UNSUPPORTED`, `XFAIL`, `ADDITIONAL_COMPILE_FLAGS` | `libcxx/test/support/` |
| `libstdcxx` | `gcc-mirror/gcc` | `gcc` stable and development | `*.cc` | `dg-do`, `dg-options`, `dg-additional-options`, `dg-require-*`, `dg-skip-if`, `dg-xfail-run-if` | `libstdc++-v3/testsuite/util/` |
| `msvc-stl` | `microsoft/STL` | `msvc` stable | `tests/std/tests/*/test.cpp` | the test directory's `env.lst` | `tests/std/include/` |

The pin is the `test_suite` key on the family's rung in
[`toolchains.json`](.github/actions/toolchain/toolchains.json), so moving a
rung moves its suite in the same edit. A release rung pins a tag; trunk has
none, so `gcc` development pins a full commit hash, which a bump moves like any
other pin while the snapshot compiler it runs on stays unpinned. Pass
`RUNG development` to read the trunk suite. The clone is shallow, partial
(`--filter=blob:none`) and sparse, covering the directories the caller takes
and the support headers. Nothing is vendored: the suites are Apache-2.0 WITH
LLVM-exception and GPL-3.0 WITH GCC-RLE, and the tests are rewritten in the
build tree only.

The suite's half is here, in [`cmake/`](cmake): where the suite lives, its pin,
its support headers, and how its markers read. The caller's half is a mapping
in TOML, kept in its own repository:

| Key | What it holds |
| :-- | :------------ |
| `take` | the directories to take, each with an optional `select`, a regular expression a file must match to be taken at all |
| `support` | support headers to rewrite as a test is, each by its `path`, for a suite that calls what the library mirrors from a helper rather than from the test itself |
| `include` | headers prepended to every rewritten test, under a `#line 1` so diagnostics keep the suite's line numbers |
| `rewrite` | `from` (literal) or `regex`, and `to`, applied in order: `std::vector<bool>` → `xstd::bit_vector` |
| `expect` | files expected to fail by design, each with its `sha256`, whether it `fails` at `compile` or `run`, and the `paragraph` the library departs from |
| `assertion` | per-line exceptions: a `line`, matched after stripping, in a `file` of a given `sha256`, replaced by `to` or dropped with `drop = true`, each naming the `departure` it stands for |

Top-level keys apply to every suite, and a table named after a suite, such as
`[libcxx]`, to that suite alone. Several mapping files are merged. A key
outside the table above, at any level, fails configuration: a misspelt
`fail` or `paragrah` would otherwise leave an expected failure unread.

A caller reaches the module through `FetchContent` of this repository at the
commit its workflows pin, behind an option that is off by default:

```cmake
FetchContent_Declare(cpp-ci GIT_REPOSITORY https://github.com/rhalbersma/cpp-ci.git GIT_TAG <the workflows' pin>)
FetchContent_MakeAvailable(cpp-ci)
include(${cpp-ci_SOURCE_DIR}/cmake/StdTestSuite.cmake)
std_test_suite(libcxx MAPPING std-test-suite.toml LINK_LIBRARIES xstd::bits)
```

Each runnable test becomes its own executable and CTest test, labelled
`std-test-suite` and its suite, and a compile test becomes an object library.
The target `std-test-suite-<suite>` builds all of them. The markers are read
against what CMake already knows: the compiler and its version, the standard
library, the dialect, the target triple, and whether exceptions and RTTI are
on. The dialect is a `-std=` in `CMAKE_CXX_FLAGS` where there is one, for a
standard CMake cannot yet name such as `-std=gnu++2d`, and `CMAKE_CXX_STANDARD`
otherwise. A test's own `-std=` gives way to the caller's. libstdc++'s
support headers include its internal headers, so its suite builds only against
libstdc++, under GCC or Clang; against another standard library every test is
skipped with that reason.

The adapter is strict where silence would mislead:

- **A stale mapping entry fails configuration.** An expected failure or an
  exception that names a file the tree no longer has, a hash its file no
  longer matches, or a line its file no longer carries, is an error. An
  upstream edit to a test is therefore reviewed, never carried forward.
- **An expected failure that starts passing turns red.** One expected at run
  must exit non-zero or crash, as a failed `assert` does, which `WILL_FAIL`
  alone would count as a failure. One expected at compile is left out of `all`
  and registered as a build of its target that must fail.
- **A test no rewrite reaches is skipped, with that reason.** It still names
  only the standard library's own types, and would pass on their account. A
  test that includes a rewritten support header counts as reached.
- **A `support` entry that does nothing fails configuration:** a header the
  tree does not have, one outside the suite's support headers, or one no
  rewrite changes.
- **Every file under a taken directory is in `manifest.json`**, beside the
  generated `tests.cmake`, with its hash and what became of it: built, run,
  expected to fail, or skipped and why. A caller can hold its own
  classification table to that list.

A rewritten support header is copied into the build tree under the name a test
includes it by, and that directory goes on the include path ahead of the
suite's own support headers, so the test's `#include` finds the copy. The copy
takes the mapping's rewrites, its prelude and any exception that names its
path, as a test does, and the manifest lists it with its hash. libc++'s
`charconv.to.chars/integral.pass.cpp` is the case it was written for: every
`std::to_chars` call in it is made from `charconv_test_helpers.h`. Two reaches
stay with the original: a test that reaches the header only through another
header, which still counts as no rewrite reaching it, and an original support
header that includes it in quotes, which finds its sibling first. Map that
header too where either matters.

Tests of diagnostics, libc++'s `.verify.cpp` and libstdc++'s `dg-error`, are a
second pass, and are skipped for now with that reason. So is a test whose
markers this adapter does not interpret: custom `RUN` lines, a lit
substitution, an unknown `dg-add-options`. Each lands in the manifest as a
skip with a reason, never as a pass.

## Future directions

Directions considered and deliberately not taken yet. Each records what it
would take, so the question can be picked up without rediscovering the answer.

### Declared support instead of dropped rungs

A caller that cannot build on a rung has one tool: leave it out of `tiers:`.
That loses the day the rung starts working, since a dropped rung is never
observed again, and it says nothing about why. The alternative is **one minimum
version per workflow**, a property of the code: the highest version any feature
it uses requires, or `∞` while nobody implements it. A *major* gap is known
from the rung's declared version before any runner starts, so that leg is
dropped; a *minor* gap is whatever the image or nightly ships that day, knowable
only on the leg, so that leg runs and is not counted. A development rung always
runs, excused rather than dropped, because trunk is where a feature appears
first.

The gate then needs per-leg outcomes rather than one `result` per matrix. A
counted leg that fails is red; an excused leg that fails is excused; and an
**excused leg that passes is red**, because its waiver is satisfied and should
be deleted. That alarm is the point of the design, and every run should also
report which legs are excused. In landing order:

1. Probe what job-level `continue-on-error` reports for a matrix, then spike
   per-leg outcomes, most likely an artifact per leg reduced by the gate, on
   `gcc.yml` alone.
2. `version` fields for `apple-clang`, `msvc` and `mingw` in the toolchain
   table, with version-aware comparison for Xcode.
3. A declaration format, most likely a stub input beside `tiers`, and the
   `toolchain` action marking legs instead of dropping them.
4. The gate: all counted legs, the excused-green alarm, the excused set
   reported.
5. Roll out across the remaining ladder workflows.
6. `self-check.yml` asking the packagers whether the ladder is stale.

Questions still open: mingw and apple-clang have no development rung, so an `∞`
waiver there has nothing able to clear it and needs a third state or an explicit
"stays dropped". A trunk rung's version number is pre-release, so comparing it
against a minimum may be the wrong instrument. An excused leg absorbs genuine
trunk regressions while it waits. Debug-only failures are bugs, not support
declarations, and stay outside the model. Two related items need none of the
gate plumbing and can land on their own: `check_internal_pins.py` checking that
the pinned commit is the newest it can be, not only that it resolves, and the
same one-commit-for-every-pin check run in each caller. Under the model a repin
stops being a no-op, and the gate should name the legs whose status the new
pin changes.

### Ladder freshness

`self-check.yml` already runs weekly. It could query each packager the ladder
draws from, without installing anything: `ppa:ubuntu-toolchain-r/test`,
apt.llvm.org (does the next suite exist), the WinLibs releases API, and the
`actions/runner-images` releases for Visual Studio and Xcode. Dependabot watches
actions; nothing watches compilers, so the ladder advances only when someone
notices.

### A test for `coverage.yml`

`self-check.yml` lints workflows but never runs one, and `coverage.yml` carries
the most logic here: the `exclude` split, the line and branch exclusion
patterns, the thresholds, and three consumers of the report files. Any of them
can break in a way that stays green, by measuring something other than what 100%
is meant to mean. The fix is a minimal C++ fixture in this repository, one
header and one Boost.Test file that covers it, and a `self-check.yml` leg
running `coverage.yml` against it, asserting that the fixture reaches 100%, that
an uncovered branch makes it **fail**, that an excluded path stays excluded, and
that the reports land where the later steps read them. The failing case matters
most: a threshold that never fires is the defect such a test exists to catch.

### Drift between callers

The callers' configuration should be identical, differing only where a library
or test dependency requires it, and the difference that does exist should be
declared rather than incidental. Some differences are deliberate and recorded so
they are not reopened: `HeaderFilterRegex` naming each library's own path, a
check suppressed for a stated reason in one caller, `exclude:` naming the tree
each caller ships, and each `vcpkg.json`. What would keep the rest from
returning is a report, not enforcement: a `self-check.yml` leg on the weekly
schedule listing, per shared configuration file, whether the callers agree, in
the step summary and never failing. A caller with a real local need should not
have to come here first, but drift should have to be looked at once and then
explained in the file or removed.

### Shared build and documentation

The same argument that moved the workflows here applies to two more things each
caller copies.

**A shared build.** The install and export rules, the package config file and
version file, `CMakePresets.json` and the test-tree conventions are boilerplate
that is a function of the project name. Two pieces carry real logic worth
writing once: the header self-sufficiency check, one generated translation unit
per public header, and one test target per test source. The version, the header
list, the language standard and anything naming the library's own types stay
per repository. One constraint shapes the whole design: **the installed package
must not depend on the shared build**, since a consumer's `find_package` reads
the installed config, so the shared build may generate that file but nothing it
provides may appear in it. `consumption.yml` is the guard. A git submodule
pinned by SHA fits the pinning convention and needs no network at configure
time, where `FetchContent` would put a download into the very consumption tests
that guard it.

**Shared documentation.** A prose site first, an API reference later or never:
none of the callers carries doc comments, and a site of bare signatures would
draw readers away from the source, where the explanations are. The pipeline
must tell two kinds of document apart. Living documents, such as a design
document or a README, are rebuilt on every push. Frozen ones, such as a WG21
paper as submitted, are published byte for byte and never re-rendered, since a
fresh render would quietly change a document of record. A `docs.yml` here would
build the one, copy the other and deploy to Pages, which needs the caller's stub
to grant `pages: write` and `id-token: write`. Examples compiled by CI and
included by reference come after there is a site to put them in.

The build goes first and the documentation after, one centralisation at a time,
so that a failure has one place to come from.

## License

<pre>
         Copyright Rein Halbersma 2026.
Distributed under the <a href="http://www.boost.org/users/license.html">Boost Software License, Version 1.0</a>.
   (See accompanying file LICENSE_1_0.txt or copy at
         <a href="http://www.boost.org/LICENSE_1_0.txt">http://www.boost.org/LICENSE_1_0.txt</a>)
</pre>
