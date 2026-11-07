#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# A header-only library and its Boost.Test suite, laid out as the callers lay
# theirs out, for self-check.yml to run coverage.yml against end to end. Its
# coverage is complete, so the gate passes at the default 100% bars.
cmake_minimum_required(VERSION 3.25)
project(cpp-ci-coverage-fixture LANGUAGES CXX)

add_library(fixture INTERFACE)
target_include_directories(fixture INTERFACE include)
target_compile_features(fixture INTERFACE cxx_std_20)

set(Boost_USE_STATIC_LIBS ON)
find_package(Boost CONFIG REQUIRED COMPONENTS unit_test_framework)

enable_testing()
add_executable(clamp test/clamp.cpp)
target_link_libraries(clamp PRIVATE fixture Boost::unit_test_framework)
add_test(NAME clamp COMMAND clamp --log_level=all)
