//          Copyright Rein Halbersma 2026.
// Distributed under the Boost Software License, Version 1.0.
//    (See accompanying file LICENSE_1_0.txt or copy at
//          http://www.boost.org/LICENSE_1_0.txt)

#define BOOST_TEST_MODULE clamp
#include <fixture/clamp.hpp>        // clamp, interval
#include <boost/test/unit_test.hpp> // BOOST_AUTO_TEST_CASE, BOOST_CHECK_EQUAL

BOOST_AUTO_TEST_CASE(EachSideOfTheInterval)
{
        constexpr auto digits = fixture::interval{0, 9};
        BOOST_CHECK_EQUAL(fixture::clamp(-1, digits), 0);
        BOOST_CHECK_EQUAL(fixture::clamp(5, digits), 5);
        BOOST_CHECK_EQUAL(fixture::clamp(10, digits), 9);
}
