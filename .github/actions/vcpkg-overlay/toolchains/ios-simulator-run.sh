#!/bin/bash
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# The ios-simulator toolchain's CMAKE_CROSSCOMPILING_EMULATOR: run one
# executable inside the booted simulator IOS_SIMULATOR_UDID names, and exit
# with its status. The simulator shares the host's file system, so the path
# ctest passes is valid inside it.
set -euo pipefail
if [ -z "${IOS_SIMULATOR_UDID:-}" ]; then
  echo "ios-simulator-run.sh: IOS_SIMULATOR_UDID names no booted simulator" >&2
  exit 1
fi
exec xcrun simctl spawn "$IOS_SIMULATOR_UDID" "$@"
