# Reusable CI for C++ projects

[![License](https://img.shields.io/badge/license-Boost-blue.svg)](https://opensource.org/licenses/BSL-1.0)
[![Actionlint](https://github.com/rhalbersma/cpp-ci/actions/workflows/actionlint.yml/badge.svg)](https://github.com/rhalbersma/cpp-ci/actions/workflows/actionlint.yml)

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

A caller supplies its own triggers, because a reusable workflow cannot carry
them, and the stub's name becomes the required status check:

```yaml
name: Sanitizers

on:
  push:
    branches: [ main ]
  pull_request:
    branches: [ main ]
  workflow_dispatch:

jobs:
  sanitizers:
    uses: rhalbersma/cpp-ci/.github/workflows/sanitizers.yml@<sha> # v1.0.0
```

A repository that needs a newer compiler than stable names the rung it needs:

```yaml
    with:
      gcc_tier: development
      clang_tier: qualification
      cxx_flags: -Wno-interference-size
```

## Workflows

### `sanitizers.yml`

Four Linux legs, each building and running the caller's test suite in Debug:

| Leg | Compiler | Flags |
| :-- | :------- | :---- |
| ASan + LSan | GCC | `-fsanitize=address -fno-omit-frame-pointer` |
| UBSan | GCC | `-fsanitize=undefined -fno-sanitize-recover=undefined` |
| UBSan | Clang | `-fsanitize=undefined -fno-sanitize-recover=undefined` |
| Implicit conversion | Clang | `-fsanitize=implicit-conversion -fno-sanitize-recover=implicit-conversion` |

Leak detection is **on**: `ASAN_OPTIONS=detect_leaks=1` is set explicitly, so a
repository that allocates gets the check rather than inheriting a suppression
written for one that does not.

UBSan runs under both compilers because the two implementations do not check
the same set. `-fsanitize=implicit-conversion` is Clang-only — `g++` rejects it
with *unrecognized argument to '-fsanitize=' option* — and catches the
value-dependent truncations and sign changes that `-Wconversion` can only
diagnose where it can prove them statically.

Deliberately absent: **TSan** (none of these libraries use threads), **MSan**
(needs an instrumented libstdc++ *and* Boost.Test), **`-fsanitize=unsigned-integer-overflow`**
(unsigned wraparound is defined and deliberate), **`_GLIBCXX_DEBUG`**
(ABI-changing, so Boost.Test would have to be rebuilt to match), and **CFI**
(no virtual dispatch). The sanitizers are Linux-only by necessity: MSVC offers
ASan alone, macOS has no LeakSanitizer, and MinGW has no usable runtime.

| Input | Default | Description |
| :---- | :------ | :---------- |
| `gcc_tier` | `stable` | Rung for the ASan and UBSan/GCC legs |
| `clang_tier` | `stable` | Rung for the two Clang legs |
| `cxx_flags` | `""` | Appended after the sanitizer's own flags |
| `cmake_args` | `""` | Extra `-D` arguments for the configure step |
| `vcpkg_triplet` | `x64-linux` | Triplet for the test dependencies |

## Actions

| Action | Purpose |
| :----- | :------ |
| `toolchain` | Resolve a (family, tier) pair to the compiler on that rung |
| `apt-retry` | Set the retry key apt actually reads, once per job |
| `install-gcc` | A GCC release from the toolchain PPA, or the trunk snapshot |
| `install-clang` | A Clang from apt.llvm.org, optionally with libc++ |

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
