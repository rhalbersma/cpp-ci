#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Chainloaded by the arm64-android-ndk triplet and by the project: the NDK's
# own toolchain at ANDROID_NDK_HOME, for arm64-v8a at the API level the
# workflow names through VCPKG_ANDROID_API_LEVEL. Executables link bionic and
# libc++ statically, so qemu-aarch64 runs them on the host kernel with no
# Android loader or system image; the API level still selects the headers
# and the startup objects each one is built against.
#
# CMake's own Android support rather than the NDK's legacy toolchain file,
# which is still the NDK's default: the legacy file overwrites the linker
# flags outright, so the -static below would never reach a link.
set(ANDROID_USE_LEGACY_TOOLCHAIN_FILE OFF)
set(ANDROID_ABI arm64-v8a)
set(ANDROID_PLATFORM "android-$ENV{VCPKG_ANDROID_API_LEVEL}")
set(ANDROID_STL c++_static)
include("$ENV{ANDROID_NDK_HOME}/build/cmake/android.toolchain.cmake")
set(CMAKE_EXE_LINKER_FLAGS_INIT -static)
set(CMAKE_CROSSCOMPILING_EMULATOR qemu-aarch64)
