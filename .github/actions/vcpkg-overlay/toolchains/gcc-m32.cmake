#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Chainloaded by the x86-linux-gcc triplet: the GCC the workflow names through
# VCPKG_GCC_VERSION, targeting i686 through its multilib.
set(CMAKE_SYSTEM_PROCESSOR i686)
set(CMAKE_C_COMPILER "gcc-$ENV{VCPKG_GCC_VERSION}")
set(CMAKE_CXX_COMPILER "g++-$ENV{VCPKG_GCC_VERSION}")
set(CMAKE_C_FLAGS_INIT "-m32")
set(CMAKE_CXX_FLAGS_INIT "-m32")
