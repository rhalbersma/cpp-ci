#!/usr/bin/env bash
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# The coverage gate, as the Coverage workflow runs it and as a developer runs it
# before pushing: one implementation, so the two cannot disagree about which
# lines count.
#
# Run from the source root, after a --coverage build whose tests have run.
# Settings come from the environment, with the workflow inputs' defaults:
#
#   GCOV                gcov                    the gcov matching the compiler that built the tests
#   BUILD_DIR           build                   where the report files go
#   EXCLUDE             test/.* build/.*        space-separated gcovr --exclude patterns
#   FAIL_UNDER_LINE     100                     line coverage below which the gate fails
#   FAIL_UNDER_BRANCH   100                     branch coverage below which the gate fails
#   SEARCH_PATHS                                space-separated trees holding the .gcda files; empty searches the root
#
# Writes BUILD_DIR/coverage.txt and BUILD_DIR/coverage.xml, and exits non-zero
# when coverage falls short of either threshold.

set -euo pipefail

gcov="${GCOV:-gcov}"
build_dir="${BUILD_DIR:-build}"
exclude="${EXCLUDE-test/.* build/.*}"
fail_under_line="${FAIL_UNDER_LINE:-100}"
fail_under_branch="${FAIL_UNDER_BRANCH:-100}"
read -ra search_paths <<< "${SEARCH_PATHS:-}"

# read -ra, not an unquoted expansion: a bare "for p in $EXCLUDE" splits AND
# globs, and "test/.*" then matches test/.clang-tidy and stops being a regex,
# leaving the test tree in the report.
read -ra patterns <<< "${exclude}"
excludes=()
for pattern in "${patterns[@]}"; do
        excludes+=(--exclude "${pattern}")
done

# Full coverage of the library's own headers, less assert(...) and
# "= default;", and less the branches gcov synthesizes that no test can take. A
# project's own assert macro, an upper-case name ending in _ASSERT, is excluded
# as assert is: it expands to the same conditional, whose false arm a passing
# test never takes.
#
# A line holding nothing but a closing brace is excluded for the same reason. It
# carries no code of the author's: what gcov records there is the unwind resume
# for a function with a local to destroy, reachable only if something inside
# throws. A test suite that passes never runs it, so every such function reads
# one line short of covered. Nothing hides behind the exclusion either - a
# function that is never called still has every line of its body counted.
#
# --merge-lines because the question this gate asks is whether a line of a
# template is ever run, not whether every emitted copy of it is. Without it
# gcov's records stay per instantiation and per translation unit, so a header
# line reads as uncovered in a unit that instantiates it and takes another
# route - which the same line, run in the unit next door, does not redeem. That
# is not a gap in the tests, and closing it means re-running identical code
# once per caller.
gcovr --root . "${search_paths[@]}" \
        --gcov-executable "${gcov}" \
        --merge-lines \
        "${excludes[@]}" \
        --exclude-lines-by-pattern '^\s*(assert|[A-Z][A-Z0-9_]*_ASSERT)\(' \
        --exclude-lines-by-pattern '=\s*default;' \
        --exclude-lines-by-pattern '\s*\}\s*$' \
        --exclude-branches-by-pattern '^\s*(assert|[A-Z][A-Z0-9_]*_ASSERT)\(' \
        --exclude-branches-by-pattern '^\s*.*=\s*default;' \
        --exclude-throw-branches \
        --exclude-unreachable-branches \
        --print-summary \
        --fail-under-line "${fail_under_line}" \
        --fail-under-branch "${fail_under_branch}" \
        --txt "${build_dir}/coverage.txt" \
        --xml-pretty --xml "${build_dir}/coverage.xml"
