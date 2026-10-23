#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Chainloaded by the arm64-ios-simulator-xcode triplet and by the project:
# the iPhone Simulator SDK of the Xcode DEVELOPER_DIR selects, for arm64, at
# the deployment target the workflow names through
# VCPKG_IOS_DEPLOYMENT_TARGET. The target is what turns libc++'s
# availability markup on, so a library call the oldest supported iOS lacks
# fails to compile here rather than to load on a device.
#
# Executables stay plain Mach-O files rather than the app bundles CMake makes
# by default for iOS, and ctest runs each through ios-simulator-run.sh inside
# the simulator the workflow booted, on that iOS release's own libc++.
set(CMAKE_SYSTEM_NAME iOS)
set(CMAKE_OSX_SYSROOT iphonesimulator)
set(CMAKE_OSX_ARCHITECTURES arm64)
set(CMAKE_OSX_DEPLOYMENT_TARGET "$ENV{VCPKG_IOS_DEPLOYMENT_TARGET}")
set(CMAKE_MACOSX_BUNDLE OFF)
set(CMAKE_CROSSCOMPILING_EMULATOR "${CMAKE_CURRENT_LIST_DIR}/ios-simulator-run.sh")
