//          Copyright Rein Halbersma 2014-2026.
// Distributed under the Boost Software License, Version 1.0.
//    (See accompanying file LICENSE_1_0.txt or copy at
//          http://www.boost.org/LICENSE_1_0.txt)

// The MSVC STL's tests read a few macros of its <yvals_core.h>, which another standard library has not.
#pragma once

#define _INLINE_VAR inline
#define _HAS_CXX17 (__cplusplus >= 201703L)
#define _HAS_CXX20 (__cplusplus >= 202002L)
#define _HAS_CXX23 (__cplusplus > 202002L)
#define _HAS_CXX26 (__cplusplus > 202302L)
