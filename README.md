# Reusable CI for C++ projects

[![License](https://img.shields.io/badge/license-Boost-blue.svg)](https://opensource.org/licenses/BSL-1.0)
[![Actionlint](https://github.com/rhalbersma/cpp-ci/actions/workflows/actionlint.yml/badge.svg)](https://github.com/rhalbersma/cpp-ci/actions/workflows/actionlint.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/rhalbersma/cpp-ci/badge)](https://scorecard.dev/viewer/?uri=github.com/rhalbersma/cpp-ci)

Shared GitHub Actions workflows for header-only C++ libraries tested with
Boost.Test through a vcpkg manifest. Calling repositories keep a thin stub per
workflow and no CI logic of their own.

## Tiers, not versions

Every toolchain is tracked on three rungs — **stable**, **qualification**, and
**development** — and callers name a rung, never a version. The rungs live in
[`.github/actions/toolchain/toolchains.json`](.github/actions/toolchain/toolchains.json),
so a compiler release is one edit here rather than one per repository, and every
caller moves up on its next pin bump.

This is the master table: what each rung resolves to, for every family the
resolver knows. A caller's own README names the rungs *it* asks for and links
here for what they are, rather than restating a version ladder that moves on a
pin bump it does not control.

| Family | Platform | Standard library | Stable | Qualification | Development |
| :----- | :------- | :--------------- | :----- | :------------ | :---------- |
| `gcc` | Linux, `ubuntu-24.04` | libstdc++ | 15 | 16 | 17-SVN |
| `clang` | Linux, `ubuntu-24.04` | libstdc++ | 22 (libstdc++ 15) | 23 (libstdc++ 16) | 24-SVN (libstdc++ 17-SVN) |
| `clang` | Linux, `ubuntu-24.04` | libc++ | 22 | 23 | 24-SVN |
| `apple-clang` | macOS | libc++ | Xcode 16.4, `macos-15` | Xcode 26.6, `macos-26` | — |
| `msvc` | Windows | MSVC STL | 2022, `windows-2022` | 2026, `windows-2025` | 2026-Preview, `windows-2025` |
| `mingw` | Windows, `windows-2022` | libstdc++ | 15 | 16 | — |

The two `clang` rows are one ladder, not two: the same release, differing in
`-stdlib=`, which is why `clang.yml` and `clang-libc++.yml` take the same rung
names. A caller's **Clang-CL** row is the `msvc` ladder too — the same rungs
with `-T ClangCL`, per [`visual-studio.yml`](#visual-studioyml) — so a change to
the MSVC rungs moves both. The clang-cl release itself is whichever one the
selected Visual Studio bundles: a runner-image fact, not something this
repository pins, and the one number in a caller's matrix that this table cannot
promise to keep current.

Not every family fills every rung — Apple publishes no Clang trunk, and WinLibs
publishes no MinGW one — so the resolver reports `supported=false` for an empty
one rather than inventing a compiler. An empty rung is a fact about the vendor;
a caller *dropping* a rung the vendor does fill is a fact about that library,
and belongs in its README rather than here.

## Usage

A caller keeps one stub per workflow, because a reusable workflow cannot carry
its own triggers. The stub's job id prefixes the status check name, so a job
`gcc` calling a workflow whose own job is named `15 Debug` appears as
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

Every workflow a caller can stub, with the defaults it gets by stubbing it
without inputs. `Gate` is the check name to tick under branch protection, with
`<job>` standing for the caller's own job id: a stub whose job is `clang_libcxx`
reports `clang_libcxx / all`.

### Building and testing

Each of these resolves the requested rungs to a matrix, builds and runs the
caller's test suite on every leg, and reports one gate whose name does not move
as the ladder does.

| Workflow | Family | Default rungs | Legs per rung | Gate |
| :------- | :----- | :------------ | :------------ | :--- |
| [`gcc.yml`](.github/workflows/gcc.yml) | `gcc` | all three | Release, Debug | `<job> / all` |
| [`clang.yml`](.github/workflows/clang.yml) | `clang`, paired libstdc++ | all three | Release, Debug | `<job> / all` |
| [`clang-libc++.yml`](.github/workflows/clang-libc++.yml) | `clang`, libc++ | all three | Release, Debug | `<job> / all` |
| [`apple-clang.yml`](.github/workflows/apple-clang.yml) | `apple-clang` | the two it fills | Release, Debug | `<job> / all` |
| [`mingw.yml`](.github/workflows/mingw.yml) | `mingw` | the two it fills | Release, Debug | `<job> / all` |
| [`visual-studio.yml`](.github/workflows/visual-studio.yml) | `msvc`, `-T` from the caller | all three | Release, Debug | `<job> / all` |
| [`sanitizers.yml`](.github/workflows/sanitizers.yml) | `gcc` and `clang` | all three, per family | five legs, Debug | `<job> / all` |
| [`consumption.yml`](.github/workflows/consumption.yml) | `gcc`, one rung | stable | one | `<job> / Consume` |

`visual-studio.yml` is stubbed twice, as `msvc` and as `clang_cl`; see
[below](#visual-studioyml). `sanitizers.yml` crosses its five legs with the
ladder rather than pinning one release — fifteen jobs by default; see
[below](#sanitizersyml).

### Analysis and quality

These do not build a matrix of the library; each answers one question about it.
A finding fails the run in every case: a check nobody has to act on stops being
a check.

| Workflow | What it runs | Default rungs | Gate |
| :------- | :----------- | :------------ | :--- |
| [`clang-tidy.yml`](.github/workflows/clang-tidy.yml) | The caller's `.clang-tidy` over the public headers, their per-header translation units and the test sources, in Debug | all three `clang` | `<job> / all` |
| [`msvc-analyze.yml`](.github/workflows/msvc-analyze.yml) | `/analyze` in the role clang-tidy fills on the other side, in Debug | all three `msvc` | `<job> / all` |
| [`coverage.yml`](.github/workflows/coverage.yml) | `gcovr`, gated at `--fail-under-line 100 --fail-under-branch 100` | stable `gcc` | `<job> / gcovr` |
| [`codeql.yml`](.github/workflows/codeql.yml) | The `c-cpp` `security-extended` query suite | stable `gcc` | `<job> / Analyze` |
| [`clang-format.yml`](.github/workflows/clang-format.yml) | `clang-format --dry-run --Werror` over `include` and `test` | stable `clang` | `<job> / clang-format` |
| [`actionlint.yml`](.github/workflows/actionlint.yml) | Actions syntax and expressions over `.github/workflows/*.yml`; ShellCheck off unless asked for | — | `<job> / actionlint` |
| [`scorecard.yml`](.github/workflows/scorecard.yml) | OpenSSF Scorecard, publishing the result the badge reads | — | none, see below |

Both analyzers default to **all three** rungs rather than one, for the reason
the ladders do: a toolset gains, renames and retires checks, so it changes the
verdict on code nobody touched. A caller whose compiler legs are green on three
rungs is claiming all three as buildable, and a reader who builds with the
newest runs *its* analyzer over their translation units. Checking one rung
answered for one of them.

`clang-format.yml` takes its own rung, defaulting to **stable** and deliberately
not following the compiler ladder: a formatter release reformats the code base,
so moving it is a decision about the repository rather than about coverage, and
a caller may well want it to lag. `coverage.yml` and `codeql.yml` sit on stable
because what they measure is the code, not the toolchain.

`scorecard.yml` cannot be a required check and is not meant to be: it runs on
pushes to the default branch and on a schedule, never on a pull request.
[`self-check.yml`](.github/workflows/self-check.yml) is this repository's own
and not for callers to stub.

Codecov, where a caller enables it, posts `codecov/project` and `codecov/patch`
on its own. Those are the caller's configuration, not this repository's.

### What a caller still documents

This repository owns what a rung resolves to, which legs a workflow runs, what
the defaults are, and what the gate is called — everything above. A caller's
README and CONTRIBUTING own the rest, and restating the tables above instead is
what lets three repositories disagree about the same CI:

- **Which families and rungs it asks for**, and where that is narrower than the
  ladder, *why* — a library its own front end cannot compile on a released MSVC
  is a fact about that library.
- **Its badges and its required-check list**, both of which are per-repository
  URLs and branch-protection settings that no shared workflow can carry.
- **Its repo-specific inputs**: the `clang-tidy` regexes, a `cxx_flags`
  workaround, a `libcxx_tiers: ""`, the `dependency_repos` a consumption leg
  needs.

The platform workflows share a shape: a first job resolves the requested rungs
to a strategy matrix, a second builds them, and a third gate reports one check
name that does not move as the ladder does.

Every rung runs on every event, **pull requests included**. `reduce_on_pr: true`
opts a caller into running the floor and the ceiling in Debug alone on a pull
request -- the oldest compiler the code claims and the one that changes weekly.
That reduction used to be the default, on the reasoning that the middle rung
has a released compiler either side of it and a push covers it within the hour.
The hour is the problem: the gate a caller marks as its required check reports
on the legs that ran, so a reduced pull request goes green having never
compiled the middle rung, and a break there merges before the push that finds
it. `sanitizers.yml` had already refused the same trade for its own reason --
what a sanitizer reports depends on the release doing the instrumenting -- and
the argument generalises.

**Concurrency belongs to the caller.** None of these declare a concurrency
group: inside a called workflow `github.workflow_ref` names the *calling*
workflow, so a group defined here would land in the caller's own group and
cancel it. The stub knows whether it was reached by a push, a schedule, or a
canary; these do not.

### `visual-studio.yml`

Named for the toolchain rather than the compiler: MSVC's `cl` and the `clang-cl`
each Visual Studio bundles are the same build in every respect but the `-T`
argument, so they are one workflow with two callers rather than two files that
would drift. A caller stubs it twice, and the two stubs are what its README
shows as separate `MSVC` and `Clang-CL` rows:

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

The development rung here is a **preview channel of the same Visual Studio
generation**, not a newer one, which is why the `msvc` ladder's qualification
and development rungs share a label and a runner. `preview_toolset_prefix` is
separate from `toolset` because that rung takes a `version=<n>` the released
ones do not.

### `consumption.yml`

One job on one rung, configuring the library with `-DBUILD_TESTING=OFF` and
then building each of `test/consumer/find_package`, `.../add_subdirectory` and
`.../fetch_content` that exists. A repository documenting two of the three
models passes; one documenting none fails, rather than reporting success
having consumed nothing.

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
        https://github.com/rhalbersma/xstd.git 7f7cbdd6e4174107ea062484f31c81fbfe220f7a
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
invalid package on the `find_package` one -- unless that dependency is really
installed first. Each repository is configured with the compiler under test
and with the prefix on its path, so a chain of them resolves in the order
given; none of them is configured against vcpkg's toolchain file, since
manifest mode keys off the tree being configured and a dependency shipping its
own `vcpkg.json` would install *that* repository's test dependencies here.

### `sanitizers.yml`

Five Linux legs, each building and running the caller's test suite in Debug,
and each run on every rung its compiler fills -- fifteen jobs, on every event.
A sanitizer's instrumentation is no more fixed across releases than across
compilers: GCC's UBSan does not diagnose the signed overflow
Clang's does on a `_BitInt(2)`, and a bit-precise library has no reason to
assume one GCC agrees with the next about that either.

| Leg | Compiler | Flags |
| :-- | :------- | :---- |
| ASan + LSan | GCC | `-fsanitize=address -fno-omit-frame-pointer` |
| ASan + LSan | Clang, libc++ | the same, plus `-stdlib=libc++` |
| UBSan | GCC | `-fsanitize=undefined -fno-sanitize-recover=undefined` |
| UBSan | Clang | `-fsanitize=undefined -fno-sanitize-recover=undefined` |
| Implicit conversion | Clang | `-fsanitize=implicit-conversion -fno-sanitize-recover=implicit-conversion` |

ASan runs against both standard libraries. Its detection is a shared runtime,
so a second compiler alone would re-run one check -- but the container-overflow
annotations are not shared: libc++ instruments `vector`, `string` and `deque`,
while libstdc++ annotates `vector` alone and only under
`_GLIBCXX_SANITIZE_VECTOR`. An overflow inside a `std::string` is invisible to
the first leg and visible to the second. That leg rebuilds Boost.Test against
libc++ through an overlay triplet, since a dependency built against the other
standard library would not link with it in any case.

Leak detection is **on**: `ASAN_OPTIONS=detect_leaks=1` is set explicitly, so a
repository that allocates gets the check rather than inheriting a suppression
written for one that does not.

The libc++ leg has its own `libcxx_tiers`, defaulting to the Clang ladder. A
library can be perfectly sound and still not build against libc++ at all -- one
missing C++23 range adaptor is enough -- and that is a fact about the standard
library rather than about the sanitizer. Left in the Clang ladder, such a
library reddens this workflow's gate permanently, which is worse than not
running the leg: a gate that is always red reports nothing about the legs that
were meant to be green. `libcxx_tiers: ""` drops it.

UBSan runs under both compilers because the two implementations do not check
the same set -- the first run of this workflow found signed overflow that GCC's
UBSan does not diagnose. `-fsanitize=implicit-conversion` is Clang-only (`g++`
rejects it) and catches the value-dependent truncations and sign changes that
`-Wconversion` can only diagnose where it proves them statically. That group
also fires on well-defined but lossy conversions, which a standard library is
full of by design, so it runs with an ignorelist confining it to the code under
test; without one it reports only on libstdc++.

Deliberately absent: **TSan** (no threads), **MSan** (needs an instrumented
libstdc++ *and* Boost.Test), **`-fsanitize=unsigned-integer-overflow`**
(the wraparound is deliberate), **`_GLIBCXX_DEBUG`** (ABI-changing, so
Boost.Test would need rebuilding to match), and **CFI** (no virtual dispatch).
Linux-only by necessity: MSVC offers ASan alone, macOS has no LeakSanitizer,
MinGW no usable runtime.

## Actions

| Action | Purpose |
| :----- | :------ |
| `toolchain` | Resolve rungs to compilers: one rung, or a whole strategy matrix |
| `apt-retry` | Set the retry key apt actually reads, once per job |
| `install-gcc` | A GCC release from the toolchain PPA, or the trunk snapshot |
| `install-clang` | A Clang from apt.llvm.org, optionally with libc++ |
| `vcpkg-overlay` | Locate the overlay triplets, which live here rather than in each caller |
| `vcpkg-install` | `vcpkg install`, retried around a download vcpkg will not retry |

A reusable workflow checks out the **caller**, not this repository, so anything
a workflow needs to read from here has to arrive as an action: an action is
fetched from its own repository and `GITHUB_ACTION_PATH` points at it. That is
why the overlay triplets are an action rather than six files copied into four
repositories.

## Conventions

Third-party actions are pinned by commit SHA with a `# vX.Y.Z` comment, and so
are the references to this repository. Resolve an annotated tag through its
peeled ref — `git ls-remote --tags` otherwise hands back the tag object, which
is not a valid pin:

```console
$ git ls-remote --tags https://github.com/rhalbersma/cpp-ci 'v1.0.0^{}'
```

Two lookups are unpinned on purpose and should stay that way: the WinLibs
release lookup and the GCC trunk `.deb`. Both move by design, and neither
upstream publishes checksums.

## License

<pre>
         Copyright Rein Halbersma 2026.
Distributed under the <a href="http://www.boost.org/users/license.html">Boost Software License, Version 1.0</a>.
   (See accompanying file LICENSE_1_0.txt or copy at
         <a href="http://www.boost.org/LICENSE_1_0.txt">http://www.boost.org/LICENSE_1_0.txt</a>)
</pre>
