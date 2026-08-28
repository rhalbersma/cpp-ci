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

| Family | Stable | Qualification | Development |
| :----- | :----- | :------------ | :---------- |
| GCC    | 15     | 16            | 17-SVN      |
| Clang  | 22 (libstdc++ 15) | 23 (libstdc++ 16) | 24-SVN (libstdc++ 17-SVN) |

Not every family fills every rung — Apple publishes no Clang trunk — so the
resolver reports `supported=false` for an empty one rather than inventing a
compiler.

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

| Workflow | What it runs |
| :------- | :----------- |
| [`gcc.yml`](.github/workflows/gcc.yml) | The GCC ladder, each rung in each build type |
| [`clang.yml`](.github/workflows/clang.yml) | The Clang ladder against its paired libstdc++ |
| [`clang-libc++.yml`](.github/workflows/clang-libc++.yml) | The same ladder against libc++ |
| [`sanitizers.yml`](.github/workflows/sanitizers.yml) | Four Linux legs; see below |
| [`consumption.yml`](.github/workflows/consumption.yml) | `find_package`, `add_subdirectory`, `FetchContent` |

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
and each run on every rung its compiler fills -- fifteen jobs, on every event. A sanitizer's instrumentation is no more fixed across releases
than across compilers: GCC's UBSan does not diagnose the signed overflow
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
