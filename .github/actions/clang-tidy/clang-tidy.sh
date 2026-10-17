#!/usr/bin/env bash
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# clang-tidy's passes over a configured build, as the Clang-Tidy workflow runs
# them and as a developer runs them before pushing: one implementation, so the
# two cannot disagree about what was checked.
#
# Run from the source root, after a configure that exported a compile database.
# Settings come from the environment, with the workflow inputs' defaults:
#
#   CLANG_TIDY_VERSION       the rung's version, naming clang-tidy-N and run-clang-tidy-N (required)
#   BUILD_DIR                build                   the configured tree holding compile_commands.json
#   INCLUDE_DIR              include                 public headers, each checked as a primary input
#   INCLUDE_EXCLUDE                                  find -path pattern for headers to skip
#   SRC_DIR                  src                     library sources, analysed from the compile database
#   TEST_DIR                 test                    test sources, likewise
#   SELF_SUFFICIENCY_REGEX                           run-clang-tidy pattern for generated per-header units
#   SOURCES_REGEX                                    deprecated explicit pattern in place of TEST_DIR
#   ADAPTED_DIRS                                     optional third-party include dirs, added with -isystem
#   REPORT                   clang-tidy-report.txt   every pass's output, appended in order
#
# Every pass runs, whatever an earlier one found, so one round reports all of
# it; the exit status is non-zero when any pass failed.

set -euo pipefail

: "${CLANG_TIDY_VERSION:?the clang-tidy version to run}"
build_dir="${BUILD_DIR:-build}"
include_dir="${INCLUDE_DIR-include}"
include_exclude="${INCLUDE_EXCLUDE:-}"
src_dir="${SRC_DIR-src}"
test_dir="${TEST_DIR-test}"
self_sufficiency_regex="${SELF_SUFFICIENCY_REGEX:-}"
sources_regex="${SOURCES_REGEX:-}"
adapted_dirs_list="${ADAPTED_DIRS:-}"
report="${REPORT:-clang-tidy-report.txt}"
database="${build_dir}/compile_commands.json"

clang_tidy="clang-tidy-${CLANG_TIDY_VERSION}"
run_clang_tidy="run-clang-tidy-${CLANG_TIDY_VERSION}"

# A collapsible section in a GitHub Actions log, a plain heading anywhere else.
begin() {
        if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::group::$1"; else echo "== $1"; fi
}
end() {
        if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::endgroup::"; fi
}
error() {
        if [ -n "${GITHUB_ACTIONS:-}" ]; then echo "::error::$1"; else echo "error: $1" >&2; fi
}

if [ ! -f "${database}" ]; then
        error "no ${database}: configure with -DCMAKE_EXPORT_COMPILE_COMMANDS=ON first"
        exit 1
fi

# Read from the compile database rather than stated here. The header pass
# drives clang-tidy outside that database and so cannot inherit the dialect;
# taking it from the build is the one way the two cannot disagree.
cxx_std=$(jq -r '.[0].command // .[0].arguments | if type == "array" then join(" ") else . end' "${database}" \
        | grep -o -- '-std=[^ ]*' | head -1 || true)
if [ -z "${cxx_std}" ]; then
        error "no -std= in ${database}"
        exit 1
fi
echo "Dialect from the build: ${cxx_std}"

# .file may be relative to .directory, so resolve before matching. Without this
# a pass would silently do nothing wherever CMake emits the relative form, which
# looks exactly like success.
mapfile -t entries < <(jq -r '.[] |
        if (.file | startswith("/")) then .file
        else .directory + "/" + .file end' "${database}")

covered() {
        local entry
        for entry in "${entries[@]}"; do
                case "${entry}" in "$1"*) return 0 ;; esac
        done
        return 1
}

# Prints the run-clang-tidy pattern for one pass, or nothing when the pass has
# nothing to run over; says which on stderr. $1 label, $2 directory, $3 explicit pattern.
resolve() {
        local label=$1 dir=$2 override=$3 abs
        if [ -n "${override}" ]; then
                echo "${label}: explicit pattern, ${override}" >&2
                echo "${override}"
        elif [ -z "${dir}" ]; then
                echo "${label}: disabled, the directory input is empty" >&2
        elif [ ! -d "${dir}" ]; then
                echo "${label}: no '${dir}' directory" >&2
        else
                # Absolute, because run-clang-tidy matches the compile database's
                # own paths; with the trailing slash, so the pattern cannot anchor
                # on a sibling whose name merely starts the same.
                abs="$(cd "${dir}" && pwd)/"
                if covered "${abs}"; then
                        echo "${label}: ${abs}.*" >&2
                        echo "${abs}.*"
                else
                        echo "${label}: '${dir}' has no compile database entry under it" >&2
                fi
        fi
}

generated=$(resolve "Generated per-header units" "" "${self_sufficiency_regex}")
library=$(resolve "Library sources" "${src_dir}" "")
tests=$(resolve "Test sources" "${test_dir}" "${sources_regex}")

: > "${report}"
failed=()

# $1 label, $2 pattern
database_pass() {
        [ -n "$2" ] || return 0
        begin "clang-tidy ${CLANG_TIDY_VERSION}: $1"
        "${run_clang_tidy}" -quiet -p "${build_dir}" "$2" 2>&1 | tee -a "${report}" || failed+=("$1")
        end
}

# Each header as the primary input too, so misc-include-cleaner can tell a
# direct include from a transitive one. Aggregates carry IWYU pragmas. Every
# header's status counts, not only the last one's.
header_pass() {
        local dir headers=() header adapted=() exclude=() status=0
        local -a adapted_dirs
        if [ -z "${include_dir}" ] || [ ! -d "${include_dir}" ]; then
                echo "Public headers: no '${include_dir}' directory"
                return 0
        fi
        begin "clang-tidy ${CLANG_TIDY_VERSION}: public headers as primary inputs"
        # read -ra, because an unquoted expansion globs as well as splits.
        read -ra adapted_dirs <<< "${adapted_dirs_list}"
        for dir in "${adapted_dirs[@]}"; do
                if [ -d "${dir}" ]; then adapted+=(-isystem "${dir}"); fi
        done
        if [ -n "${include_exclude}" ]; then exclude=(! -path "${include_exclude}"); fi
        mapfile -d '' headers < <(find "${include_dir}" -type f -name '*.hpp' "${exclude[@]}" -print0 | sort -z)
        for header in "${headers[@]}"; do
                "${clang_tidy}" "${header}" -- -x c++-header "${cxx_std}" -I"${include_dir}" "${adapted[@]}" \
                        2>&1 | tee -a "${report}" || status=1
        done
        end
        if [ "${status}" -ne 0 ]; then failed+=("public headers as primary inputs"); fi
}

database_pass "generated per-header units" "${generated}"
header_pass
database_pass "library sources" "${library}"
database_pass "test sources" "${tests}"

findings=$(grep -cE ': (error|warning):' "${report}" || true)
if [ "${#failed[@]}" -ne 0 ]; then
        error "clang-tidy ${CLANG_TIDY_VERSION}: ${findings} findings; failed: $(IFS=,; echo "${failed[*]}")"
        exit 1
fi
echo "clang-tidy ${CLANG_TIDY_VERSION}: ${findings} findings, every pass clean"
