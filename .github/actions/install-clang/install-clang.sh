#!/usr/bin/env bash
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Install a Clang from apt.llvm.org, naming the key and suite directly rather than running llvm.sh. The action beside
# this script runs it with its inputs as the environment below; anything else that installs what CI builds with can
# run it the same way.
#
#   VERSION      major version, e.g. 22
#   LLVM_SUFFIX  the apt.llvm.org suite's suffix: a versioned one such as -22, or empty for the development branch
#   LIBCXX       true to install libc++ alongside (default false)
#   FORMAT       true to install clang-format alongside (default false)
#   TIDY         true to install clang-tidy, and its run-clang-tidy, alongside (default false)

set -euo pipefail

: "${VERSION:?set VERSION to the Clang major version}"
LLVM_SUFFIX="${LLVM_SUFFIX-}"
LIBCXX="${LIBCXX:-false}"
FORMAT="${FORMAT:-false}"
TIDY="${TIDY:-false}"
# Outside an action, the signing key is downloaded to a scratch directory.
RUNNER_TEMP="${RUNNER_TEMP:-$(mktemp -d)}"

# wget's own --tries does not retry a failed TLS handshake, which apt.llvm.org drops intermittently.
key="$RUNNER_TEMP/apt.llvm.org.asc"
for i in 1 2 3 4 5; do
        if wget -qO "$key" --tries=3 --waitretry=5 --retry-on-http-error=429,500,502,503,504 https://apt.llvm.org/llvm-snapshot.gpg.key; then break; fi
        if [ "$i" -eq 5 ]; then
                echo "::error::could not download the apt.llvm.org signing key"
                exit 1
        fi
        echo "::warning::apt.llvm.org signing key download failed, retrying (attempt $i of 5)"
        sleep $((i * 10))
done
sudo install -m 644 "$key" /etc/apt/trusted.gpg.d/apt.llvm.org.asc
rm "$key"
codename=$(lsb_release -cs)
# Every apt call runs under a time limit: a mirror that stops answering
# mid-transfer holds apt past its own timeouts, for hours, where a
# failed step costs minutes.
sudo timeout --kill-after=30s 10m add-apt-repository -y "deb https://apt.llvm.org/${codename}/ llvm-toolchain-${codename}${LLVM_SUFFIX} main"
sudo timeout --kill-after=30s 10m apt-get update

packages=(
        "clang-${VERSION}"
        "lldb-${VERSION}"
        "lld-${VERSION}"
        "clangd-${VERSION}"
)
if [ "$LIBCXX" = "true" ]; then
        packages+=("libc++-${VERSION}-dev" "libc++abi-${VERSION}-dev")
fi
if [ "$FORMAT" = "true" ]; then
        packages+=("clang-format-${VERSION}")
fi
if [ "$TIDY" = "true" ]; then
        packages+=("clang-tidy-${VERSION}")
fi
sudo timeout --kill-after=30s 20m apt-get install -y "${packages[@]}"
