# Reusable CI for C++ projects

[![License](https://img.shields.io/badge/license-Boost-blue.svg)](https://opensource.org/licenses/BSL-1.0)
[![Actionlint](https://github.com/rhalbersma/cpp-ci/actions/workflows/actionlint.yml/badge.svg)](https://github.com/rhalbersma/cpp-ci/actions/workflows/actionlint.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/rhalbersma/cpp-ci/badge)](https://scorecard.dev/viewer/?uri=github.com/rhalbersma/cpp-ci)

Shared GitHub Actions workflows for header-only C++ libraries whose test
dependencies come from a `vcpkg.json` manifest and whose tests are registered
with CTest. No test framework is named anywhere here — Boost.Test, Catch2 and
GoogleTest all work, since a leg installs whatever the manifest declares and
then runs `ctest`. Calling repositories keep a thin stub per workflow and no
CI logic of their own.

*Continuous integration* here is the broad sense: not unit testing alone, but
every check a change should survive before it merges.

| Kind of check | What it establishes | Workflows |
| :------------ | :------------------ | :-------- |
| Unit testing | the library compiles, and its tests pass, on every rung named | the six [ladders](#unit-testing) |
| Sanitizers | it does so with no undefined behaviour, bad access or leak | [`sanitizers.yml`](#sanitizersyml) |
| Coverage | every line and branch is exercised | `coverage.yml` |
| Static analysis | no finding from a linter or a security query suite | `clang-tidy.yml`, `msvc-analyze.yml`, `codeql.yml` |
| Code formatting | the tree matches the caller's `.clang-format` | `clang-format.yml` |
| Workflow checks | the Actions files are valid and the supply chain pinned | `actionlint.yml`, `scorecard.yml` |
| Consumability | the installed package can actually be consumed | `consumption.yml` |

The rest of this file is in the order a caller needs it.
[Tiers](#tiers-not-versions) is what a rung resolves to,
[Usage](#usage) is what a stub contains, [Workflows](#workflows) is what each
one runs and what its gate is called, and [Actions](#actions) and
[Conventions](#conventions) are the pieces underneath. Each section opens with
a table and spells out below it only what a table cannot carry.

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

## Workflows

Sixteen workflows, and the eight that compile and run the caller's test suite
are the ones that say most; this table sorts all of them by what each does with
that suite.

| The caller's test suite | Workflows |
| :---------------------- | :-------- |
| Configured, built and run under `ctest` | the six ladder workflows, `sanitizers.yml`, `coverage.yml` |
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

### `sanitizers.yml`

Five Linux legs, each building and running the caller's test suite in Debug on
every rung its compiler fills — fifteen jobs by default, on every event.

| Leg | Compiler | Flags |
| :-- | :------- | :---- |
| ASan + LSan | GCC | `-fsanitize=address -fno-omit-frame-pointer` |
| ASan + LSan | Clang, libc++ | the same, plus `-stdlib=libc++` |
| UBSan | GCC | `-fsanitize=undefined -fno-sanitize-recover=undefined` |
| UBSan | Clang | `-fsanitize=undefined -fno-sanitize-recover=undefined` |
| Implicit conversion | Clang | `-fsanitize=implicit-conversion -fno-sanitize-recover=implicit-conversion` |

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

### Analysis and quality

The remaining eight, each answering one narrower question about the library
rather than building a matrix of it.

| Workflow | What it runs | Default rungs | Gate |
| :------- | :----------- | :------------ | :--- |
| [`clang-tidy.yml`](.github/workflows/clang-tidy.yml) | The caller's `.clang-tidy` over the public headers, their per-header translation units and the test sources | all three `clang` | `<job> / all` |
| [`msvc-analyze.yml`](.github/workflows/msvc-analyze.yml) | `/analyze`, in the role clang-tidy fills on the other side | all three `msvc` | `<job> / all` |
| [`coverage.yml`](.github/workflows/coverage.yml) | `ctest`, then `gcovr` gated at `--fail-under-line 100 --fail-under-branch 100` | stable `gcc` | `<job> / gcovr` |
| [`codeql.yml`](.github/workflows/codeql.yml) | The `c-cpp` `security-extended` query suite | stable `gcc` | `<job> / Analyze` |
| [`consumption.yml`](#consumptionyml) | `find_package`, `add_subdirectory` and `FetchContent` against the installed package | stable `gcc` | `<job> / Consume` |
| [`clang-format.yml`](.github/workflows/clang-format.yml) | `clang-format --dry-run --Werror` over `include` and `test` | stable `clang` | `<job> / clang-format` |
| [`actionlint.yml`](.github/workflows/actionlint.yml) | Actions syntax and expressions over `.github/workflows/*.yml`; ShellCheck off unless asked for | — | `<job> / actionlint` |
| [`scorecard.yml`](.github/workflows/scorecard.yml) | OpenSSF Scorecard, publishing the result the badge reads | — | none, see below |

A finding fails the run in every case: a check nobody has to act on stops being
a check.

Both analyzers run on **all three** rungs rather than one, for the reason the
ladders do: a toolset gains, renames and retires checks, so it changes the
verdict on code nobody touched. A caller whose compiler legs are green on three
rungs is claiming all three as buildable, and a reader who builds with the
newest runs *its* analyzer over their translation units. Both take Debug, since
`-DNDEBUG` rewrites every `assert` before the analyzer sees it.

`clang-format.yml` takes its own rung, defaulting to **stable** and
deliberately not following the compiler ladder: a formatter release reformats
the code base, so moving it is a decision about the repository rather than
about coverage, and a caller may well want it to lag. `coverage.yml` and
`codeql.yml` sit on stable because what they measure is the code, not the
toolchain.

`scorecard.yml` cannot be a required check and is not meant to be: it runs on
pushes to the default branch and on a schedule, never on a pull request.
[`self-check.yml`](.github/workflows/self-check.yml) is this repository's own
and not for callers to stub. Codecov, where a caller enables it, posts
`codecov/project` and `codecov/patch` on its own; those are the caller's
configuration, not this repository's.

### `visual-studio.yml`

One workflow stubbed twice, once per toolset, which is what a caller's README
shows as its separate `MSVC` and `Clang-CL` rows.

| Stub job | `toolset` | `preview_toolset_prefix` | `cache_prefix` |
| :------- | :-------- | :----------------------- | :------------- |
| `msvc` | default, `host=x64` | default, empty | default, `msvc` |
| `clang_cl` | `ClangCL,host=x64` | `"ClangCL,"` | `clang-cl` |

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

The two need separate caches because they build the dependencies with
different compilers. The development rung here is a **preview channel of the
same Visual Studio generation**, not a newer one, which is why the `msvc`
ladder's qualification and development rungs share a label and a runner.
`preview_toolset_prefix` is separate from `toolset` because that rung takes a
`version=<n>` the released ones do not.

### `consumption.yml`

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
install *that* repository's test dependencies here.

### What a caller still documents

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

### Loosening and tightening the ladder

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

Six composite actions, which exist because a reusable workflow checks out the
caller rather than this repository.

| Action | Purpose |
| :----- | :------ |
| `toolchain` | Resolve rungs to compilers: one rung, or a whole strategy matrix |
| `apt-retry` | Set the retry key apt actually reads, once per job |
| `install-gcc` | A GCC release from the toolchain PPA, or the trunk snapshot |
| `install-clang` | A Clang from apt.llvm.org, optionally with libc++ |
| `vcpkg-overlay` | Locate the overlay triplets, which live here rather than in each caller |
| `vcpkg-install` | `vcpkg install`, retried around a download vcpkg will not retry |

Anything a workflow needs to read from here therefore has to arrive as an
action: an action is fetched from its own repository and `GITHUB_ACTION_PATH`
points at it. That is why the overlay triplets are an action rather than six
files copied into four repositories.

## Conventions

References are pinned by commit SHA, with two lookups left unpinned on purpose.

| What | How it is pinned |
| :--- | :--------------- |
| Third-party actions | commit SHA, with a `# vX.Y.Z` comment |
| References to this repository | commit SHA, with a `# vX.Y.Z` comment |
| The WinLibs release lookup | unpinned, by design |
| The GCC trunk `.deb` | unpinned, by design |

Resolve an annotated tag through its peeled ref — `git ls-remote --tags`
otherwise hands back the tag object, which is not a valid pin:

```console
$ git ls-remote --tags https://github.com/rhalbersma/cpp-ci 'v1.0.0^{}'
```

The two unpinned lookups should stay that way: both move by design, and
neither upstream publishes checksums.

## License

<pre>
         Copyright Rein Halbersma 2026.
Distributed under the <a href="http://www.boost.org/users/license.html">Boost Software License, Version 1.0</a>.
   (See accompanying file LICENSE_1_0.txt or copy at
         <a href="http://www.boost.org/LICENSE_1_0.txt">http://www.boost.org/LICENSE_1_0.txt</a>)
</pre>
