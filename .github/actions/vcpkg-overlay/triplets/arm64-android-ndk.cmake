#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# arm64-v8a Android, built with the NDK at the API level the android-ndk
# workflow names: the dependencies for its legs, sharing the project's
# toolchain, libc++ and API level. vcpkg's own arm64-android triplet fixes the
# API level at 28 and links libc++ as a shared library, which no Android
# loader is present to find when the tests run under qemu-aarch64. Both
# variables are passed through to the port builds, and vcpkg hashes their
# values into each package's ABI, so the three levels never share a cached
# package.
set(VCPKG_TARGET_ARCHITECTURE arm64)
set(VCPKG_CRT_LINKAGE static)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_CMAKE_SYSTEM_NAME Android)
set(VCPKG_CMAKE_SYSTEM_VERSION $ENV{VCPKG_ANDROID_API_LEVEL})
set(VCPKG_MAKE_BUILD_TRIPLET "--host=aarch64-linux-android")
set(VCPKG_ENV_PASSTHROUGH ANDROID_NDK_HOME VCPKG_ANDROID_API_LEVEL)
set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE ${CMAKE_CURRENT_LIST_DIR}/../toolchains/android-ndk.cmake)
