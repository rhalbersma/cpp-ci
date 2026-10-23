#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# arm64 iOS Simulator, built with the Xcode DEVELOPER_DIR selects at the
# deployment target the ios workflow names: the dependencies for its legs,
# sharing the project's SDK, libc++ availability and deployment target.
# vcpkg's community arm64-ios-simulator triplet leaves the deployment target
# to the SDK's default, which is the SDK's own release, so a port would be
# built for a newer iOS than the tests it links into. Both variables are
# passed through to the port builds, and vcpkg hashes their values into each
# package's ABI, so legs on different Xcodes or targets never share one.
set(VCPKG_TARGET_ARCHITECTURE arm64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_CMAKE_SYSTEM_NAME iOS)
set(VCPKG_OSX_SYSROOT iphonesimulator)
set(VCPKG_OSX_DEPLOYMENT_TARGET $ENV{VCPKG_IOS_DEPLOYMENT_TARGET})
set(VCPKG_ENV_PASSTHROUGH DEVELOPER_DIR VCPKG_IOS_DEPLOYMENT_TARGET)
set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE ${CMAKE_CURRENT_LIST_DIR}/../toolchains/ios-simulator.cmake)
