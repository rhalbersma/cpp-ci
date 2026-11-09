#!/usr/bin/env bash
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Installs on an Ubuntu machine the compilers and tools the shared workflows build and check with, at the versions
# this repository pins, so a local run builds with what CI builds with. A caller runs it from a checkout of this
# repository at the commit its own workflows pin, which is what keeps the two from disagreeing.
#
#   setup-toolchain.sh [--stable] [--requirements FILE]... [--boost LIB[,LIB...]]...
#
# Installed, each read from where the workflows read it:
#
#   GCC and Clang at every rung of .github/actions/toolchain/toolchains.json, GCC's development rung as the trunk
#   snapshot under g++-N; clang-tidy for each Clang; clang-format and libc++ at the stable rung alone; and the stable
#   GCC's 32-bit multilib, for i686 legs;
#   gcovr, from .github/actions/install-gcovr/requirements.txt, pinned by hash;
#   actionlint, at the release the actionlint workflow builds, checked against that release's checksums, outside
#   --stable;
#   vcpkg, at the commit the vcpkg-setup action checks out, in VCPKG_ROOT or ~/.local/share/cpp-ci/vcpkg, outside
#   --stable;
#   with --boost, the Boost release that commit resolves, built from Boost's CMake release tarball.
#
# --stable installs the stable rungs' GCC and Clang alone, with gcovr and the requirements files: what a job that builds
# on that rung needs, without actionlint and vcpkg, which it never runs and which would only add GitHub downloads that
# can fail. --requirements installs a caller's own
# hash-pinned requirements file into ~/.local, such as the CMake or the FileCheck its workflows pin; it repeats.
# --boost names Boost libraries, by the names Boost's CMake build takes, such as hash2,test,unordered; it repeats,
# and every library named is built by the stable GCC into BOOST_PREFIX, else /usr/local. Outside --stable, where the
# stable GCC's multilib is installed, the same libraries are built again at -m32 into <prefix>/i686. This is for a
# machine whose network refuses the source archives vcpkg's Boost ports download, though it serves release assets;
# tools/ci_local.py --deps system then builds against it, and tools/boost-test.sh builds the Boost.Test a compiler on
# another standard library links.
#
# Each step checks before it acts, so a second run installs nothing and finishes in seconds. Apt sources are written
# as files rather than through add-apt-repository, which needs the system Python's apt_pkg module and fails on a
# machine whose default python3 lacks it. libc++ stays at the stable rung: apt's libc++ packages exclude one another,
# and CI gives each libc++ leg a runner of its own.

set -euo pipefail

usage="usage: $0 [--stable] [--requirements FILE]... [--boost LIB[,LIB...]]..."
stable_only=0
requirements=()
boost_listed=()
while [ "$#" -gt 0 ]; do
        case "$1" in
                --stable) stable_only=1 ;;
                --requirements) shift; requirements+=("$(realpath "${1:?--requirements needs a file}")") ;;
                --boost) shift; IFS=, read -ra listed <<< "${1:?--boost needs a library list}"; boost_listed+=("${listed[@]}") ;;
                *) echo "${usage}" >&2; exit 2 ;;
        esac
        shift
done
readonly stable_only
# The 32-bit multilib, and the Boost built against it, exist on x86-64 alone; elsewhere, such as an arm64 runner, the
# stable rung installs without them.
machine="$(uname -m)"
readonly machine
boost_libraries=()
for library in "${boost_listed[@]}"; do
        library="${library// /}"
        [ -n "${library}" ] || continue
        if [[ ! "${library}" =~ ^[a-z0-9_]+$ ]]; then
                echo "error: --boost takes Boost's library names, such as hash2 or dynamic_bitset, not '${library}'" >&2
                exit 2
        fi
        boost_libraries+=("${library}")
done
mapfile -t boost_libraries < <(printf '%s\n' "${boost_libraries[@]}" | sort -u | sed '/^$/d')
readonly boost_libraries

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ROOT
export DEBIAN_FRONTEND=noninteractive

# A cloud setup script runs as root; a developer's shell does not.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
        SUDO="sudo"
fi

# One field of one rung of the toolchain table, as the toolchain action reads it.
rung() {
        python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]][sys.argv[3]][sys.argv[4]])' \
                "${ROOT}/.github/actions/toolchain/toolchains.json" "$@"
}

gcc_stable="$(rung gcc stable version)"
clang_stable="$(rung clang stable version)"
gcc_versions=("${gcc_stable}")
clang_rungs=(stable)
gcc_trunk=""
if [ "${stable_only}" -eq 0 ]; then
        for tier in qualification development; do
                if [ "$(rung gcc "${tier}" trunk)" = "true" ]; then
                        gcc_trunk="$(rung gcc "${tier}" version)"
                else
                        gcc_versions+=("$(rung gcc "${tier}" version)")
                fi
        done
        clang_rungs+=(qualification development)
fi

codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"

# Writes the file only when its content would change, and reports whether it did.
write_if_changed() {
        local file="$1" content="$2"
        if [ "$($SUDO cat "${file}" 2>/dev/null)" = "${content}" ]; then
                return 1
        fi
        printf '%s\n' "${content}" | $SUDO tee "${file}" > /dev/null
}

# apt.llvm.org for Clang, the toolchain PPA for a GCC newer than the release's. The PPA signs with RSA-1024, so apt
# warns about a weak algorithm on every update; that is the archive's key, not a fault in this script.
sources_changed=0
if [ ! -s /usr/share/keyrings/llvm.gpg ]; then
        curl -fsSL --retry 8 --retry-delay 15 https://apt.llvm.org/llvm-snapshot.gpg.key | $SUDO gpg --batch --yes --dearmor -o /usr/share/keyrings/llvm.gpg
fi
if [ ! -s /usr/share/keyrings/ubuntu-toolchain.gpg ]; then
        curl -fsSL --retry 8 --retry-delay 15 "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x60c317803a41ba51845e371a1e9377a2ba9ef27f" \
                | $SUDO gpg --batch --yes --dearmor -o /usr/share/keyrings/ubuntu-toolchain.gpg
fi
llvm_sources=""
for tier in "${clang_rungs[@]}"; do
        llvm_sources+="deb [signed-by=/usr/share/keyrings/llvm.gpg] https://apt.llvm.org/${codename}/ llvm-toolchain-${codename}$(rung clang "${tier}" llvm_suffix) main"$'\n'
done
write_if_changed /etc/apt/sources.list.d/cpp-ci-llvm.list "${llvm_sources%$'\n'}" && sources_changed=1
write_if_changed /etc/apt/sources.list.d/cpp-ci-ubuntu-toolchain.list \
        "deb [signed-by=/usr/share/keyrings/ubuntu-toolchain.gpg] https://ppa.launchpadcontent.net/ubuntu-toolchain-r/test/ubuntu ${codename} main" \
        && sources_changed=1

packages=("clang-format-${clang_stable}" "libc++-${clang_stable}-dev" "libc++abi-${clang_stable}-dev")
for version in "${gcc_versions[@]}"; do
        packages+=("g++-${version}")
done
[ "${stable_only}" -eq 1 ] || [ "${machine}" != x86_64 ] || packages+=("g++-${gcc_stable}-multilib")
for tier in "${clang_rungs[@]}"; do
        version="$(rung clang "${tier}" version)"
        packages+=("clang-${version}" "clang-tidy-${version}")
done

missing=()
for package in "${packages[@]}"; do
        dpkg-query -W -f='${Status}' "${package}" 2>/dev/null | grep -q "install ok installed" || missing+=("${package}")
done
# apt-get update exits 0 when a source refuses its index, and apt then reads a package name it has no index for as a
# pattern: during a 503 brownout on ppa.launchpadcontent.net, "g++-15" installed clang-15 and flang-15 and exited 0.
# A package counts as available only where its own name heads apt-cache policy with a candidate, and the install
# names that exact version, which apt never reads as a pattern. The update and the install are retried together,
# each under a time limit, since a mirror that stops answering holds apt past its own timeouts.
candidate() {
        apt-cache policy "$1" | awk -v header="$1:" '
                $0 == header { found = 1; next }
                /^[^ ]/ { found = 0 }
                found && $1 == "Candidate:" { print $2; exit }'
}
if [ "${#missing[@]}" -gt 0 ] || [ "${sources_changed}" -eq 1 ]; then
        for attempt in 1 2 3 4 5; do
                $SUDO timeout --kill-after=30s 10m apt-get update -qq || true
                exact=()
                for package in "${packages[@]}"; do
                        version="$(candidate "${package}")"
                        if [ -n "${version}" ] && [ "${version}" != "(none)" ]; then
                                exact+=("${package}=${version}")
                        fi
                done
                if [ "${#exact[@]}" -eq "${#packages[@]}" ] && $SUDO timeout --kill-after=30s 20m apt-get install -y -qq "${exact[@]}"; then
                        break
                fi
                if [ "${attempt}" -eq 5 ]; then
                        echo "error: ${packages[*]} not all installable after five attempts" >&2
                        exit 1
                fi
                sleep $((attempt * 10))
        done
fi

# GCC trunk is Jonathan Wakely's weekly binary snapshot, the one the development rung installs, under g++-N.
# Upstream publishes neither checksums nor signatures, so what arrived is recorded instead. Its ETag is kept beside
# it, so a rerun downloads only a snapshot newer than the one installed.
if [ -n "${gcc_trunk}" ]; then
        snapshot_url=https://kayari.org/gcc-latest/gcc-latest.deb
        snapshot_etag_file=/opt/gcc-latest/.etag
        snapshot_etag="$(curl -fsSIL "${snapshot_url}" | tr -d '\r' | sed -n 's/^[Ee][Tt]ag: //p' | tail -1 || true)"
        if [ ! -x /opt/gcc-latest/bin/g++ ] \
                || { [ -n "${snapshot_etag}" ] && [ "$(cat "${snapshot_etag_file}" 2>/dev/null)" != "${snapshot_etag}" ]; }; then
                snapshot="$(mktemp -d)"
                curl -fsSL --retry 8 --retry-delay 15 "${snapshot_url}" -o "${snapshot}/gcc-latest.deb"
                dpkg-deb -f "${snapshot}/gcc-latest.deb" Package Version
                sha256sum "${snapshot}/gcc-latest.deb"
                $SUDO dpkg -i "${snapshot}/gcc-latest.deb" > /dev/null || $SUDO timeout --kill-after=30s 20m apt-get install -f -y -qq
                rm -rf "${snapshot}"
                [ -z "${snapshot_etag}" ] || printf '%s\n' "${snapshot_etag}" | $SUDO tee "${snapshot_etag_file}" > /dev/null
        fi
        for tool in gcc g++ gcov; do
                [ "$(readlink "/usr/bin/${tool}-${gcc_trunk}")" = "/opt/gcc-latest/bin/${tool}" ] \
                        || $SUDO ln -sf "/opt/gcc-latest/bin/${tool}" "/usr/bin/${tool}-${gcc_trunk}"
        done
        if write_if_changed /etc/ld.so.conf.d/gcc-latest.conf "$(printf '%s\n' /opt/gcc-latest/lib /opt/gcc-latest/lib64)"; then
                $SUDO ldconfig
        fi
fi

local_bin="${HOME:-/root}/.local/bin"
mkdir -p "${local_bin}"

# Installs a hash-pinned requirements file into ~/.local unless every package it pins is already at its version.
pip_install_pinned() {
        local file="$1" name version
        while read -r name version; do
                [ "$(python3 -m pip show "${name}" 2>/dev/null | sed -n 's/^Version: //p')" = "${version}" ] || {
                        python3 -m pip install --quiet --user --break-system-packages --require-hashes -r "${file}" \
                                || python3 -m pip install --quiet --user --require-hashes -r "${file}"
                        return
                }
        done < <(sed -n 's/^\([A-Za-z0-9_.-]*\)==\([^ ;\\]*\).*/\1 \2/p' "${file}")
}
pip_install_pinned "${ROOT}/.github/actions/install-gcovr/requirements.txt"
for file in "${requirements[@]}"; do
        pip_install_pinned "${file}"
done

# actionlint at the release the actionlint workflow builds with go install, here from that release's binary,
# checked against the checksums file published beside it. Not under --stable.
if [ "${stable_only}" -eq 0 ]; then
actionlint_version="$(sed -n "s/.*inputs\.actionlint_version || '\(v[0-9.]*\)'.*/\1/p" "${ROOT}/.github/workflows/actionlint.yml")"
: "${actionlint_version:?no actionlint version in .github/workflows/actionlint.yml}"
if ! "${local_bin}/actionlint" --version 2>/dev/null | grep -qx "${actionlint_version#v}"; then
        release="https://github.com/rhysd/actionlint/releases/download/${actionlint_version}"
        case "${machine}" in aarch64) platform=linux_arm64 ;; *) platform=linux_amd64 ;; esac
        archive="actionlint_${actionlint_version#v}_${platform}.tar.gz"
        scratch="$(mktemp -d)"
        curl -fsSL --retry 8 --retry-delay 15 "${release}/${archive}" -o "${scratch}/${archive}"
        curl -fsSL --retry 8 --retry-delay 15 "${release}/actionlint_${actionlint_version#v}_checksums.txt" -o "${scratch}/checksums.txt"
        (cd "${scratch}" && grep " ${archive}\$" checksums.txt | sha256sum --check --quiet)
        tar -xzf "${scratch}/${archive}" -C "${scratch}" actionlint
        install -m 755 "${scratch}/actionlint" "${local_bin}/actionlint"
        rm -rf "${scratch}"
fi
fi

# vcpkg at the commit the vcpkg-setup action checks out, fetched by its SHA without history, and bootstrapped. Not
# under --stable.
if [ "${stable_only}" -eq 0 ]; then
vcpkg_commit="$(sed -n '/^  commit:/,/^  [a-z]/s/^    default: \([0-9a-f]\{40\}\)$/\1/p' "${ROOT}/.github/actions/vcpkg-setup/action.yml")"
: "${vcpkg_commit:?no vcpkg commit in .github/actions/vcpkg-setup/action.yml}"
vcpkg_root="${VCPKG_ROOT:-${HOME:-/root}/.local/share/cpp-ci/vcpkg}"
if [ "$(git -C "${vcpkg_root}" rev-parse HEAD 2>/dev/null)" != "${vcpkg_commit}" ] || [ ! -x "${vcpkg_root}/vcpkg" ]; then
        mkdir -p "${vcpkg_root}"
        [ -d "${vcpkg_root}/.git" ] || git init -q "${vcpkg_root}"
        git -C "${vcpkg_root}" fetch -q --depth 1 https://github.com/microsoft/vcpkg "${vcpkg_commit}"
        git -C "${vcpkg_root}" checkout -q FETCH_HEAD
        "${vcpkg_root}/bootstrap-vcpkg.sh" -disableMetrics > /dev/null
fi
fi

# Boost from its CMake release tarball, at the release vcpkg's pinned commit resolves, pinned by the SHA-256 Boost
# publishes beside it. Boost's CMake build, unlike b2, installs a CMake package for each header-only library too,
# Boost.Hash2 among them. A library counts as installed where its package is, at this release, beside BoostConfig;
# Boost.Test's package is boost_unit_test_framework. Only what is missing triggers a build, of every library named.
# -m32 goes to the C compiler too, since Boost.Container compiles a C source.
readonly BOOST_VERSION=1.92.0
readonly BOOST_SHA256=9bed76128d4e46755dbe818487788c6fceb6f72b378f4daa49b7e1e600d9088d
boost_prefix="${BOOST_PREFIX:-/usr/local}"
boost_installed() {
        local prefix="$1" library package
        [ -f "${prefix}/lib/cmake/Boost-${BOOST_VERSION}/BoostConfig.cmake" ] || return 1
        for library in "${boost_libraries[@]}"; do
                package="${library}"
                [ "${library}" != test ] || package=unit_test_framework
                compgen -G "${prefix}/lib/cmake/boost_${package}-${BOOST_VERSION}*" > /dev/null || return 1
        done
}
boost_targets=("${boost_prefix}:")
[ "${stable_only}" -eq 1 ] || [ "${machine}" != x86_64 ] || boost_targets+=("${boost_prefix}/i686:-m32")
boost=""
if [ "${#boost_libraries[@]}" -gt 0 ]; then
        cmake="${local_bin}/cmake"
        [ -x "${cmake}" ] || cmake=cmake
        for target in "${boost_targets[@]}"; do
                prefix="${target%%:*}"
                flags="${target#*:}"
                boost_installed "${prefix}" && continue
                if [ -z "${boost}" ]; then
                        boost="$(mktemp -d)"
                        curl -fsSL --retry 8 --retry-delay 15 "https://github.com/boostorg/boost/releases/download/boost-${BOOST_VERSION}/boost-${BOOST_VERSION}-cmake.tar.xz" \
                                -o "${boost}/boost.tar.xz"
                        echo "${BOOST_SHA256}  ${boost}/boost.tar.xz" | sha256sum --check --quiet
                        tar -xf "${boost}/boost.tar.xz" -C "${boost}"
                fi
                install_sudo="${SUDO}"
                if mkdir -p "${prefix}" 2> /dev/null && [ -w "${prefix}" ]; then
                        install_sudo=""
                fi
                "${cmake}" -S "${boost}/boost-${BOOST_VERSION}" -B "${boost}/build${flags}" -DCMAKE_BUILD_TYPE=Release \
                        -DCMAKE_C_COMPILER="gcc-${gcc_stable}" -DCMAKE_CXX_COMPILER="g++-${gcc_stable}" \
                        -DCMAKE_C_FLAGS="${flags}" -DCMAKE_CXX_FLAGS="${flags}" -DBUILD_SHARED_LIBS=OFF \
                        -DBOOST_INCLUDE_LIBRARIES="$(IFS=';'; echo "${boost_libraries[*]}")" > /dev/null
                "${cmake}" --build "${boost}/build${flags}" --parallel "$(nproc)" > /dev/null
                ${install_sudo} "${cmake}" --install "${boost}/build${flags}" --prefix "${prefix}" > /dev/null
        done
        [ -z "${boost}" ] || rm -rf "${boost}"
fi

# Report what landed, but never fail a setup over a version banner: everything above has already installed.
for version in "${gcc_versions[@]}" ${gcc_trunk}; do
        "g++-${version}" --version | head -1 || true
done
for tier in "${clang_rungs[@]}"; do
        "clang++-$(rung clang "${tier}" version)" --version | head -1 || true
done
"clang-format-${clang_stable}" --version || true
"${local_bin}/gcovr" --version | head -1 || true
if [ "${stable_only}" -eq 0 ]; then
        echo "actionlint $("${local_bin}/actionlint" --version | head -1)" || true
        echo "vcpkg ${vcpkg_commit} in ${vcpkg_root}"
fi
if [ "${#boost_libraries[@]}" -gt 0 ]; then
        for target in "${boost_targets[@]}"; do
                flags="${target#*:}"
                echo "Boost ${BOOST_VERSION} (${boost_libraries[*]}) in ${target%%:*}${flags:+ at ${flags}}"
        done
fi
