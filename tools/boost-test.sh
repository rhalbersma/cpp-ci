#!/usr/bin/env bash
#          Copyright Rein Halbersma 2014-2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Builds Boost.Test's framework from the installed Boost headers with the compiler given, and writes it as a CMake
# package, so a compiler on another standard library or target links a Boost.Test built against that library. The one
# setup-toolchain.sh --boost installs is the stable GCC's: linked with libc++, with the trunk snapshot's libstdc++ or
# at -m32, its test runner fails to link or fails before the first test. CI gets the same from vcpkg, which builds
# Boost with the compiler under test.
#
#   tools/boost-test.sh <output directory> <C++ compiler> [compiler flags...]
#
# prints the -D that makes the test suite find it:
#
#   cmake -S . -B build -DCMAKE_CXX_COMPILER=clang++-22 -DCMAKE_CXX_FLAGS=-stdlib=libc++ \
#         -DCMAKE_EXE_LINKER_FLAGS=-stdlib=libc++ "$(tools/boost-test.sh build-boost-test clang++-22 -stdlib=libc++)"
#
# The flags are the ones that choose the standard library or the target, such as -stdlib=libc++,
# --gcc-toolchain=/opt/gcc-latest or -m32; a Boost outside the compiler's search path takes -isystem <prefix>/include.

set -euo pipefail

if [ "$#" -lt 2 ]; then
        echo "usage: $0 <output directory> <C++ compiler> [compiler flags...]" >&2
        exit 2
fi
out="$(mkdir -p "$1" && cd "$1" && pwd)"
cxx="$2"
shift 2

# The installed Boost's version, which BoostConfig asks of each component exactly. The include trace is read whole
# rather than piped, since a reader that stops at the first match would end the compiler on SIGPIPE.
if ! trace="$(printf '#include <boost/version.hpp>\n' | "$cxx" "$@" -x c++ -E -H -o /dev/null - 2>&1)"; then
        printf '%s\n' "${trace}" >&2
        echo "error: ${cxx} $* finds no <boost/version.hpp>: run setup-toolchain.sh --boost test" >&2
        exit 1
fi
version_header="$(sed -n 's/^\.* *\(.*boost\/version\.hpp\)$/\1/p;T;q' <<< "${trace}")"
[ -f "${version_header}" ] || { echo "error: no <boost/version.hpp> in ${cxx}'s include trace" >&2; exit 1; }
lib_version="$(sed -n 's/^#define BOOST_LIB_VERSION "\(.*\)"$/\1/p' "${version_header}")"
version="$(tr '_' '.' <<< "${lib_version}")"
[ "$(tr -cd '.' <<< "${version}" | wc -c)" -eq 2 ] || version="${version}.0"

# The unit test framework's sources, as Boost.Test's own build lists them, compiled side by side; any that fails
# fails the build, rather than leave an archive short of an object.
sources=(compiler_log_formatter debug decorator execution_monitor framework junit_log_formatter plain_report_formatter
         progress_monitor results_collector results_reporter test_framework_init_observer test_tools test_tree
         unit_test_log unit_test_main unit_test_monitor unit_test_parameters xml_log_formatter xml_report_formatter)
objects=()
pids=()
for source in "${sources[@]}"; do
        printf '#include <boost/test/impl/%s.ipp>\n' "${source}" | "$cxx" "$@" -O1 -fPIC -x c++ -c - -o "${out}/${source}.o" &
        pids+=("$!")
        objects+=("${out}/${source}.o")
done
status=0
for pid in "${pids[@]}"; do
        wait "${pid}" || status=1
done
if [ "${status}" -ne 0 ]; then
        echo "error: Boost.Test's framework does not compile with ${cxx} $*" >&2
        exit 1
fi
ar rcs "${out}/libboost_unit_test_framework.a" "${objects[@]}"
rm -f "${objects[@]}"

cat > "${out}/boost_unit_test_framework-config.cmake" <<CONFIG
# Boost.Test ${version}, built by tools/boost-test.sh with ${cxx} $*.
if(NOT TARGET Boost::unit_test_framework)
    add_library(Boost::unit_test_framework STATIC IMPORTED)
    set_target_properties(Boost::unit_test_framework PROPERTIES
        IMPORTED_LOCATION "\${CMAKE_CURRENT_LIST_DIR}/libboost_unit_test_framework.a"
        INTERFACE_LINK_LIBRARIES Boost::headers
    )
endif()
CONFIG
cat > "${out}/boost_unit_test_framework-config-version.cmake" <<CONFIG
set(PACKAGE_VERSION ${version})
set(PACKAGE_VERSION_COMPATIBLE TRUE)
if(PACKAGE_FIND_VERSION VERSION_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
endif()
CONFIG

echo "-Dboost_unit_test_framework_DIR=${out}"
