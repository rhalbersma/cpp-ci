#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Run TEST and pass only when it fails, by a non-zero exit or by a crash. CTest's
# WILL_FAIL inverts an exit status but not a signal, and a failed assert aborts.
#
#   cmake -DTEST=<executable> -P std_test_suite_expect_failure.cmake

execute_process(COMMAND "${TEST}" RESULT_VARIABLE result)
if(result STREQUAL "0")
        message(FATAL_ERROR "${TEST} passes, but is expected to fail")
endif()
message(STATUS "${TEST} fails, as expected: ${result}")
