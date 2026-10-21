#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# s390x-linux, built with the distribution's s390x cross GCC: the dependencies
# for the s390x workflow's big-endian leg, sharing the project's s390x
# libstdc++. vcpkg's community s390x-linux triplet alone builds with the host
# compiler, since vcpkg's Linux toolchain picks a cross compiler only for the
# architectures it lists, and s390x is not among them.
set(VCPKG_TARGET_ARCHITECTURE s390x)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_CMAKE_SYSTEM_NAME Linux)
set(VCPKG_CHAINLOAD_TOOLCHAIN_FILE ${CMAKE_CURRENT_LIST_DIR}/../toolchains/gcc-s390x.cmake)
