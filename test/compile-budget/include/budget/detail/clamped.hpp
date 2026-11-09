//          Copyright Rein Halbersma 2026.
// Distributed under the Boost Software License, Version 1.0.
//    (See accompanying file LICENSE_1_0.txt or copy at
//          http://www.boost.org/LICENSE_1_0.txt)

#ifndef BUDGET_DETAIL_CLAMPED_HPP
#define BUDGET_DETAIL_CLAMPED_HPP

// Compiles only after <fixture/clamp.hpp>, from a dependency repository, so on its own it fails.
namespace budget::detail {

template<int Lo, int Hi>
inline constexpr auto clamped = fixture::interval{ Lo, Hi };

}       // namespace budget::detail

#endif  // include guard
