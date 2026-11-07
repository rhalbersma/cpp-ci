//          Copyright Rein Halbersma 2026.
// Distributed under the Boost Software License, Version 1.0.
//    (See accompanying file LICENSE_1_0.txt or copy at
//          http://www.boost.org/LICENSE_1_0.txt)

#ifndef FIXTURE_CLAMP_HPP
#define FIXTURE_CLAMP_HPP

#include <cassert> // assert

namespace fixture {

struct interval
{
        int lo;
        int hi;
};

[[nodiscard]] constexpr auto clamp(int x, interval i) noexcept
        -> int
{
        assert(i.lo <= i.hi);
        if (x < i.lo) {
                return i.lo;
        }
        if (i.hi < x) {
                return i.hi;
        }
        return x;
}

}       // namespace fixture

#endif  // include guard
