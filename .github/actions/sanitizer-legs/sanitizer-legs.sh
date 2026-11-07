#!/usr/bin/env bash
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# The sanitizer ladder's legs, crossed with the compiler rungs each runs on: the one table the Sanitizers workflow
# and a developer's local run share. legs.json beside this script lists the legs; each GCC leg crosses with the GCC
# rungs, each Clang leg with the Clang rungs, and the libc++ leg with the libc++ rungs.
#
#   GCC_RUNGS     the toolchain action's matrix output for the GCC rungs, {"include":[...]}
#   CLANG_RUNGS   the same for the Clang rungs
#   LIBCXX_RUNGS  the same for the libc++ rungs; empty drops the libc++ leg
#
# Prints the crossed matrix, {"include":[...]}, one object per leg and rung with the leg's keys and the rung's, and
# under GitHub Actions also writes it to GITHUB_OUTPUT as "matrix".
#
# Why these legs:
#
# Leak detection deliberately left on. A library that allocates nothing has nothing to report, and the repositories
# that do allocate are the ones that leg exists for.
#
# ASan runs against both standard libraries. Its detection is a shared runtime, so a second compiler alone would
# re-run one check -- but the container-overflow annotations are not shared: libc++ instruments vector, string and
# deque, libstdc++ annotates vector alone and only under _GLIBCXX_SANITIZE_VECTOR.
#
# UBSan twice over, because the two implementations do not check the same set and a clean run under one says nothing
# about the other.
#
# implicit-conversion is Clang's alone -- g++ rejects the group -- and catches the value-dependent truncations and sign
# changes that -Wconversion can only diagnose where it proves them statically.
#
# No -g among the flags: every leg builds Debug, so the debug level a symbolized report reads is
# CMAKE_CXX_FLAGS_DEBUG, which is debug_flags. A -g here would come first on the line and lose.

set -euo pipefail

: "${GCC_RUNGS:?set GCC_RUNGS to the GCC rungs matrix}"
: "${CLANG_RUNGS:?set CLANG_RUNGS to the Clang rungs matrix}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# A skipped step has no outputs, so the empty case arrives as the empty string rather than as JSON. No rungs means
# the libc++ leg crosses with nothing and drops out below.
libcxx_rungs="${LIBCXX_RUNGS:-}"
if [ -z "$libcxx_rungs" ]; then
        libcxx_rungs='{"include":[]}'
        if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::notice::libcxx_tiers is empty, dropping the ASan libc++ leg"; fi
fi

matrix=$(jq -c -n \
        --slurpfile legs "$here/legs.json" \
        --argjson gcc "$GCC_RUNGS" \
        --argjson clang "$CLANG_RUNGS" \
        --argjson libcxx "$libcxx_rungs" \
        '{include: [ $legs[0][] as $leg
           | (if $leg.family == "gcc" then $gcc
              elif $leg.libcxx then $libcxx
              else $clang end).include[] as $rung
           | $leg + $rung ]}')

if [ -n "${GITHUB_OUTPUT:-}" ]; then
        echo "matrix=$matrix" >> "$GITHUB_OUTPUT"
        jq -r '.include | "\(length) legs:", (.[] | "  \(.name) / \(.compiler) \(.label)")' <<< "$matrix"
else
        echo "$matrix"
fi
