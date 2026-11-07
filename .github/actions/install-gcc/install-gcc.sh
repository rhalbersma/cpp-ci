#!/usr/bin/env bash
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Install a GCC by major version: a release from the Ubuntu toolchain PPA, or from the Launchpad librarian when the
# PPA's mirror refuses it, or GCC trunk from Jonathan Wakely's binary snapshot. The action beside this script runs it
# with its inputs as the environment below; anything else that installs what CI builds with can run it the same way.
#
#   VERSION           major version, e.g. 15; with TRUNK, the version the snapshot reports
#   TRUNK             true for the trunk snapshot, false for a release
#   SYMLINK_BINARIES  true to put gcc-N and g++-N on PATH for a trunk install (default true)
#   MULTILIB          true to bring the 32-bit runtime with a release, for -m32 (default false)
#   SOURCE            ppa, or librarian to skip the PPA (default ppa)

set -eo pipefail

: "${VERSION:?set VERSION to the GCC major version}"
: "${TRUNK:?set TRUNK to true or false}"
SYMLINK_BINARIES="${SYMLINK_BINARIES:-true}"
MULTILIB="${MULTILIB:-false}"
SOURCE="${SOURCE:-ppa}"
# Outside an action: the librarian's downloads go to a scratch directory, and librarian.py is beside this script.
RUNNER_TEMP="${RUNNER_TEMP:-$(mktemp -d)}"
GITHUB_ACTION_PATH="${GITHUB_ACTION_PATH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

install_release() {
        installed=false
        if [ "$SOURCE" = "ppa" ]; then
                # add-apt-repository resolves a ppa: shorthand through Launchpad's
                # web API via launchpadlib, before apt is involved at all, so the
                # Acquire::Retries setting does not cover it: that governs apt's own
                # downloads. A truncated response (IncompleteRead) or a key the API
                # will not serve yet (GPGKeyTemporarilyNotFoundError) fails the step
                # outright, so retry the call itself.
                #
                # Five attempts backing off to half a minute, not three to ten
                # seconds. The shorter budget was spent in sixty-five seconds on a
                # GPGKeyTemporarilyNotFoundError while a sibling leg installing the
                # same package on the same image at the same second succeeded -- the
                # brownout simply outlasted it.
                #
                # Every apt call runs under a time limit. A mirror that stops answering
                # mid-transfer holds apt past its own timeouts: on 2026-11-05 three jobs
                # sat in this step for four to five hours, one of them in a single
                # apt-get update, each holding a runner until it was cancelled. Under
                # the limit a stall is one more attempt
                # of the loop it is in, or a failed step, in minutes rather than hours.
                added=false
                for i in 1 2 3 4 5; do
                        if sudo timeout --kill-after=30s 10m add-apt-repository -y ppa:ubuntu-toolchain-r/test; then added=true; break; fi
                        if [ "$i" -lt 5 ]; then sleep $((i * 10)); fi
                done

                # apt-get update exits 0 when a source fails, keeping whatever index it
                # had for it, which for a PPA added a moment ago is none. apt then reads
                # a package name it cannot find as a regular expression, and "g++-15"
                # matches clang-15, flang-15 and llvm-15: during a 503 brownout on
                # ppa.launchpadcontent.net, apt-get install "g++-15" installed those
                # and exited 0, and the leg failed later at a missing compiler.
                # apt-cache policy falls back to a regular expression too, so a package
                # counts as available only where its own name heads the output with an
                # install candidate, and the install names that exact version, which
                # apt never reads as a pattern. A pool download refused with a 503
                # fails the install after Acquire::Retries, so the update and the
                # install are retried together, on add-apt-repository's backoff.
                candidate() {
                        apt-cache policy "$1" | awk -v header="$1:" '
                                $0 == header { found = 1; next }
                                /^[^ ]/ { found = 0 }
                                found && $1 == "Candidate:" { print $2; exit }'
                }
                packages=("g++-${VERSION}")
                if [ "$MULTILIB" = "true" ]; then
                        packages+=("g++-${VERSION}-multilib")
                fi
                for i in 1 2 3 4 5; do
                        if [ "$added" != "true" ]; then break; fi
                        sudo timeout --kill-after=30s 10m apt-get update || true
                        exact=()
                        for package in "${packages[@]}"; do
                                version=$(candidate "$package")
                                if [ -n "$version" ] && [ "$version" != "(none)" ]; then
                                        exact+=("${package}=${version}")
                                fi
                        done
                        if [ "${#exact[@]}" -eq "${#packages[@]}" ] && sudo timeout --kill-after=30s 20m apt-get install -y "${exact[@]}"; then
                                installed=true
                                break
                        fi
                        if [ "$i" -lt 5 ]; then sleep $((i * 10)); fi
                done
                if [ "$installed" != "true" ]; then
                        echo "::warning::${packages[*]} not installed from ppa:ubuntu-toolchain-r/test after five attempts; taking the same packages from the Launchpad librarian"
                fi
        fi

        # ppa.launchpadcontent.net, the mirror apt reads the PPA through, can
        # refuse nearly every request for hours while Launchpad itself is up:
        # on 2026-10-21 it answered 503 from 15:19 UTC into the night. The
        # librarian still serves the PPA's packages, so librarian.py fetches
        # the set apt would take from the PPA, each checked against the sha256
        # the Launchpad API reports, and apt installs them as local files.
        # The PPA's source goes first: with it in place, apt would reach for
        # the mirror again to resolve anything in the set.
        if [ "$installed" != "true" ]; then
                sudo rm -f /etc/apt/sources.list.d/ubuntu-toolchain-r-ubuntu-test-*
                sudo timeout --kill-after=30s 10m apt-get update
                debs="${RUNNER_TEMP}/gcc-${VERSION}-librarian"
                librarian=(python3 -I "${GITHUB_ACTION_PATH}/librarian.py" --version "$VERSION" --dest "$debs")
                if [ "$MULTILIB" = "true" ]; then
                        librarian+=(--multilib)
                fi
                "${librarian[@]}"
                sudo timeout --kill-after=30s 20m apt-get install -y "$debs"/*.deb
        fi
        if ! command -v "g++-${VERSION}" > /dev/null; then
                echo "::error::g++-${VERSION} is not on PATH after its install"
                exit 1
        fi
}

install_trunk() {
        set -u
        if [ "$MULTILIB" = "true" ]; then
                echo "::error::the GCC trunk snapshot has no 32-bit multilib; leave the development rung out of an i686 ladder"
                exit 1
        fi
        # Jonathan Wakely's binary snapshot of GCC trunk
        # (https://jwakely.github.io/pkg-gcc-latest/), required like any leg.
        wget -q --tries=3 --waitretry=5 --retry-on-http-error=429,500,502,503,504 https://kayari.org/gcc-latest/gcc-latest.deb -O gcc-latest.deb
        # Deliberately the moving URL, so this leg keeps tracking trunk; upstream
        # publishes neither checksums nor signatures, so record what arrived.
        dpkg-deb -f gcc-latest.deb Package Version
        sha256sum gcc-latest.deb
        # Under a time limit, as the release install's apt calls are.
        sudo dpkg -i gcc-latest.deb || sudo timeout --kill-after=30s 20m apt-get install -f -y

        if [ "$SYMLINK_BINARIES" = "true" ]; then
                sudo ln -sf /opt/gcc-latest/bin/gcc        "/usr/bin/gcc-${VERSION}"
                sudo ln -sf /opt/gcc-latest/bin/g++        "/usr/bin/g++-${VERSION}"
                sudo ln -sf /opt/gcc-latest/bin/gcov       "/usr/bin/gcov-${VERSION}"
                sudo ln -sf /opt/gcc-latest/bin/gcov-dump  "/usr/bin/gcov-dump-${VERSION}"
                sudo ln -sf /opt/gcc-latest/bin/gcov-tool  "/usr/bin/gcov-tool-${VERSION}"
                sudo ln -sf /opt/gcc-latest/bin/gcc-ar     "/usr/bin/gcc-ar-${VERSION}"
                sudo ln -sf /opt/gcc-latest/bin/gcc-nm     "/usr/bin/gcc-nm-${VERSION}"
                sudo ln -sf /opt/gcc-latest/bin/gcc-ranlib "/usr/bin/gcc-ranlib-${VERSION}"
        fi

        printf '%s\n' \
                '# Multiarch support' \
                /opt/gcc-latest/lib \
                /opt/gcc-latest/lib64 | sudo tee /etc/ld.so.conf.d/gcc-latest.conf > /dev/null
        sudo ldconfig
}

if [ "$TRUNK" = "true" ]; then
        install_trunk
else
        install_release
fi
