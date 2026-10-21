#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Chainloaded by the s390x-linux-gcc triplet: the s390x cross GCC the workflow
# names through VCPKG_GCC_VERSION. A port's try_run executes under qemu-s390x,
# with the cross toolchain's sysroot as the root its loader is found under.
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR s390x)
set(CMAKE_C_COMPILER "s390x-linux-gnu-gcc-$ENV{VCPKG_GCC_VERSION}")
set(CMAKE_CXX_COMPILER "s390x-linux-gnu-g++-$ENV{VCPKG_GCC_VERSION}")
set(CMAKE_CROSSCOMPILING_EMULATOR qemu-s390x -L /usr/s390x-linux-gnu)
