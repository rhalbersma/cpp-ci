//          Copyright Rein Halbersma 2026.
// Distributed under the Boost Software License, Version 1.0.
//    (See accompanying file LICENSE_1_0.txt or copy at
//          http://www.boost.org/LICENSE_1_0.txt)

#ifndef BUDGET_CLAMPED_HPP
#define BUDGET_CLAMPED_HPP

#include <fixture/clamp.hpp>         // clamp, interval
#include <budget/detail/clamped.hpp> // clamped

namespace budget {

[[nodiscard]] constexpr auto percent(int x) noexcept
        -> int
{
        return fixture::clamp(x, detail::clamped<0, 100>);
}

}       // namespace budget

#endif  // include guard
