#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# vcpkg's community wasm32-emscripten, with C++ exceptions compiled in.
# Emscripten turns them off by default, so a throw aborts; a port built that
# way cannot hand an exception to a test built with them on, and Boost.Test
# reports a failed check by throwing. Every object in the link gets the flag.
include("${VCPKG_ROOT_DIR}/triplets/community/wasm32-emscripten.cmake")
set(VCPKG_C_FLAGS "-fexceptions")
set(VCPKG_CXX_FLAGS "-fexceptions")
set(VCPKG_LINKER_FLAGS "-fexceptions")
