#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# Run the libc++, libstdc++ or MSVC STL test suite against a library that
# mirrors part of the standard library. std_test_suite.py beside this file does
# the work: it fetches the suite at the tag toolchains.json pins on the given
# rung, reads the suite's own markers, rewrites a build-tree copy of each test
# per the consumer's mapping, and writes tests.cmake, which this module
# includes. Configuration fails on a mapping entry the fetched tree no longer
# matches, so an upstream edit to a test is reviewed rather than carried.
#
#   std_test_suite(<suite>
#                  MAPPING <file>...
#                  LINK_LIBRARIES <target>...
#                  [NAME <name>]
#                  [RUNG <stable|qualification|development>]
#                  [TAG <tag>]
#                  [WARN_STALE])
#
# <suite> is libcxx, libstdcxx or msvc-stl. <name>, by default <suite>, names
# this run's targets, tests, label and build directory, so one suite can run
# against several subjects, each with a mapping of its own: libc++'s set tests
# against two set types, say, as NAME libcxx and NAME libcxx-fixed-set. Each
# runnable test becomes its own executable and CTest test, labelled
# std-test-suite and <name>; a compile
# test becomes an object library. A test the suite or the mapping expects to
# fail at run must exit non-zero or crash; one expected to fail at compile is
# left out of all and registered as a build of its target that must fail, so
# an expected failure that starts passing turns red rather than staying
# excused. A test of diagnostics is the same build, which must also report
# every error the suite's markers name: libc++'s .verify.cpp compiles under
# Clang's -verify, and libstdc++'s dg-error lines are matched against GCC's
# output. The target std-test-suite-<name> builds every test that is meant
# to build.
#
# WARN_STALE is for a run against a trunk the mapping was not written for: a
# stale mapping entry is reported rather than failing configuration, an entry
# whose file merely hashes differently still applies where its line still
# reads, and the manifest lists every stale entry. The tests then show what the
# next release changes and breaks: each is left out of all and built by a CTest
# test of its own, which a run test of it requires, so a test trunk broke fails
# alone and the build of the consumer's tree still passes.
#
# Needs git and a Python of 3.11 or later, for tomllib. A consumer reaches this
# module through FetchContent of this repository at the commit its workflows
# pin, behind an option that is off by default.

include_guard(GLOBAL)

set(STD_TEST_SUITE_SCRIPT "${CMAKE_CURRENT_LIST_DIR}/std_test_suite.py")
set(STD_TEST_SUITE_EXPECT_FAILURE "${CMAKE_CURRENT_LIST_DIR}/std_test_suite_expect_failure.cmake")

function(std_test_suite_add name)
        cmake_parse_arguments(PARSE_ARGV 1 arg "" "SUITE;KIND;SOURCE;EXPECT;DIAGNOSTICS;WORKING_DIRECTORY" "INCLUDE_DIRS;OPTIONS;LINK_OPTIONS")
        string(REPLACE "-" "_" suite_var "${arg_SUITE}")
        if(arg_KIND STREQUAL "compile" OR arg_KIND STREQUAL "diagnose")
                add_library(${name} OBJECT "${arg_SOURCE}")
        else()
                add_executable(${name} "${arg_SOURCE}")
        endif()
        target_include_directories(${name} PRIVATE ${arg_INCLUDE_DIRS} ${STD_TEST_SUITE_INCLUDE_DIRS_${suite_var}})
        target_link_libraries(${name} PRIVATE ${STD_TEST_SUITE_LINK_LIBRARIES})
        target_compile_options(${name} PRIVATE ${arg_OPTIONS})
        # A marker's -l names an archive, which the link reads only after the objects that need it.
        target_link_libraries(${name} PRIVATE ${arg_LINK_OPTIONS})
        set(labels std-test-suite ${arg_SUITE})
        if(NOT arg_EXPECT STREQUAL "compile" AND NOT arg_KIND STREQUAL "diagnose" AND NOT STD_TEST_SUITE_DEFERRED)
                add_dependencies(std-test-suite-${arg_SUITE} ${name})
        endif()
        if(arg_KIND STREQUAL "diagnose")
                set_target_properties(${name} PROPERTIES EXCLUDE_FROM_ALL TRUE)
                add_test(NAME ${name} COMMAND ${Python3_EXECUTABLE} -I ${STD_TEST_SUITE_SCRIPT} --check-diagnostics ${arg_DIAGNOSTICS}
                         -- ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR} --target ${name} --config $<CONFIG>)
                set_tests_properties(${name} PROPERTIES LABELS "${labels}" RESOURCE_LOCK std-test-suite-build)
        elseif(arg_EXPECT STREQUAL "compile")
                set_target_properties(${name} PROPERTIES EXCLUDE_FROM_ALL TRUE)
                add_test(NAME ${name} COMMAND ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR} --target ${name} --config $<CONFIG>)
                # Builds of a shared tree do not run side by side.
                set_tests_properties(${name} PROPERTIES WILL_FAIL TRUE LABELS "${labels}" RESOURCE_LOCK std-test-suite-build)
        elseif(STD_TEST_SUITE_DEFERRED)
                # A run against a trunk builds each test in a CTest test of its own, out of all, so a test that trunk
                # broke fails alone, the rest still build and run, and the build of the tree it sits in still passes.
                set_target_properties(${name} PROPERTIES EXCLUDE_FROM_ALL TRUE)
                add_test(NAME ${name}.build COMMAND ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR} --target ${name} --config $<CONFIG>)
                set_tests_properties(${name}.build PROPERTIES LABELS "${labels}" RESOURCE_LOCK std-test-suite-build FIXTURES_SETUP ${name})
                if(arg_KIND STREQUAL "run" AND arg_EXPECT STREQUAL "run")
                        add_test(NAME ${name} COMMAND ${CMAKE_COMMAND} -DTEST=$<TARGET_FILE:${name}> -P ${STD_TEST_SUITE_EXPECT_FAILURE})
                elseif(arg_KIND STREQUAL "run")
                        add_test(NAME ${name} COMMAND ${name})
                endif()
                if(arg_KIND STREQUAL "run")
                        set_tests_properties(${name} PROPERTIES LABELS "${labels}" FIXTURES_REQUIRED ${name})
                endif()
        elseif(arg_KIND STREQUAL "run" AND arg_EXPECT STREQUAL "run")
                # WILL_FAIL would count a failed assert's abort as a failure, not the one expected.
                add_test(NAME ${name} COMMAND ${CMAKE_COMMAND} -DTEST=$<TARGET_FILE:${name}> -P ${STD_TEST_SUITE_EXPECT_FAILURE})
                set_tests_properties(${name} PROPERTIES LABELS "${labels}")
        elseif(arg_KIND STREQUAL "run")
                add_test(NAME ${name} COMMAND ${name})
                set_tests_properties(${name} PROPERTIES LABELS "${labels}")
        endif()
        if(arg_WORKING_DIRECTORY AND arg_KIND STREQUAL "run")
                set_tests_properties(${name} PROPERTIES WORKING_DIRECTORY "${arg_WORKING_DIRECTORY}")
        endif()
endfunction()

function(std_test_suite suite)
        cmake_parse_arguments(PARSE_ARGV 1 arg "WARN_STALE" "NAME;RUNG;TAG" "MAPPING;LINK_LIBRARIES")
        if(NOT arg_MAPPING)
                message(FATAL_ERROR "std_test_suite(${suite}): MAPPING names no file")
        endif()
        if(NOT arg_RUNG)
                set(arg_RUNG stable)
        endif()
        if(NOT arg_NAME)
                set(arg_NAME ${suite})
        endif()
        find_package(Python3 3.11 REQUIRED COMPONENTS Interpreter)

        # The compiler as the suites' markers name it.
        if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
                set(compiler gcc)
        elseif(CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang")
                set(compiler apple-clang)
        elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang" AND CMAKE_CXX_COMPILER_FRONTEND_VARIANT STREQUAL "MSVC")
                set(compiler msvc)
        elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
                set(compiler clang)
        elseif(CMAKE_CXX_COMPILER_ID STREQUAL "MSVC")
                set(compiler msvc)
        else()
                message(FATAL_ERROR "std_test_suite(${suite}): no marker vocabulary for ${CMAKE_CXX_COMPILER_ID}")
        endif()

        # The standard library, from the macro each one defines in <version>.
        include(CheckCXXSymbolExists)
        check_cxx_symbol_exists(_LIBCPP_VERSION version STD_TEST_SUITE_HAS_LIBCPP)
        check_cxx_symbol_exists(__GLIBCXX__ version STD_TEST_SUITE_HAS_LIBSTDCXX)
        if(STD_TEST_SUITE_HAS_LIBCPP)
                set(stdlib libc++)
        elseif(STD_TEST_SUITE_HAS_LIBSTDCXX)
                set(stdlib libstdc++)
        else()
                set(stdlib msvc)
        endif()

        if(CMAKE_CXX_COMPILER_TARGET)
                set(target ${CMAKE_CXX_COMPILER_TARGET})
        elseif(compiler STREQUAL "msvc")
                string(TOLOWER "${CMAKE_CXX_COMPILER_ARCHITECTURE_ID}" arch)
                set(target "${arch}-pc-windows-msvc")
        else()
                execute_process(COMMAND ${CMAKE_CXX_COMPILER} -dumpmachine OUTPUT_VARIABLE target OUTPUT_STRIP_TRAILING_WHITESPACE)
        endif()

        # The dialect the markers are read against: a -std= in the flags, for one CMake cannot yet name, or else CMAKE_CXX_STANDARD.
        set(flags "${CMAKE_CXX_FLAGS}")
        set(std ${CMAKE_CXX_STANDARD})
        if(flags MATCHES "-std=(c|gnu)\\+\\+([0-9][0-9a-z])")
                # A draft's spelling stands for its standard's year, and 98 for 03, the dialect it shares.
                set(year ${CMAKE_MATCH_2})
                foreach(draft IN ITEMS 98:03 0x:11 1y:14 1z:17 2a:20 2b:23 2c:26 2d:29)
                        if(draft MATCHES "^${year}:(..)$")
                                set(year ${CMAKE_MATCH_1})
                        endif()
                endforeach()
                string(REGEX REPLACE "^0" "" std ${year})
        endif()
        if(NOT std)
                message(FATAL_ERROR "std_test_suite(${suite}): set CMAKE_CXX_STANDARD, the dialect the suites' markers are read against")
        endif()

        set(extra)
        if(flags MATCHES "-fno-exceptions")
                list(APPEND extra --no-exceptions)
        endif()
        if(flags MATCHES "-fno-rtti|/GR-")
                list(APPEND extra --no-rtti)
        endif()
        # What links std::stacktrace: nothing more with libstdc++ 16 and later, -lstdc++exp with 14 and 15.
        if(stdlib STREQUAL "libstdc++")
                include(CheckCXXSourceCompiles)
                set(stacktrace_program "#include <stacktrace>\nint main() { return std::stacktrace::current().empty() ? 1 : 0; }")
                check_cxx_source_compiles("${stacktrace_program}" STD_TEST_SUITE_STACKTRACE_LINKS)
                if(NOT STD_TEST_SUITE_STACKTRACE_LINKS)
                        set(CMAKE_REQUIRED_LIBRARIES stdc++exp)
                        check_cxx_source_compiles("${stacktrace_program}" STD_TEST_SUITE_STACKTRACE_LINKS_WITH_EXP)
                        unset(CMAKE_REQUIRED_LIBRARIES)
                        if(STD_TEST_SUITE_STACKTRACE_LINKS_WITH_EXP)
                                list(APPEND extra --stacktrace-link=-lstdc++exp)
                        endif()
                endif()
        endif()
        if(arg_TAG)
                list(APPEND extra --tag ${arg_TAG})
        endif()
        if(arg_WARN_STALE)
                list(APPEND extra --warn-stale)
        endif()
        set(mappings)
        foreach(file IN LISTS arg_MAPPING)
                get_filename_component(file "${file}" ABSOLUTE)
                list(APPEND mappings --mapping "${file}")
        endforeach()

        set(binary_dir "${CMAKE_CURRENT_BINARY_DIR}/std-test-suite/${arg_NAME}")
        execute_process(
                COMMAND ${Python3_EXECUTABLE} -I "${STD_TEST_SUITE_SCRIPT}"
                        --suite ${suite} --name ${arg_NAME} ${mappings}
                        --tree "${CMAKE_BINARY_DIR}/_deps/std-test-suite-${arg_NAME}"
                        --binary-dir "${binary_dir}"
                        --rung ${arg_RUNG}
                        --compiler ${compiler} --version ${CMAKE_CXX_COMPILER_VERSION}
                        --stdlib ${stdlib} --std ${std} --target ${target}
                        ${extra}
                RESULT_VARIABLE result
        )
        if(NOT result EQUAL 0)
                message(FATAL_ERROR "std_test_suite(${suite}): the adapter failed; see the lines above")
        endif()
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${arg_MAPPING} "${STD_TEST_SUITE_SCRIPT}" "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../.github/actions/toolchain/toolchains.json")

        set(STD_TEST_SUITE_LINK_LIBRARIES ${arg_LINK_LIBRARIES})
        set(STD_TEST_SUITE_DEFERRED ${arg_WARN_STALE})
        if(NOT TARGET std-test-suite-${arg_NAME})
                add_custom_target(std-test-suite-${arg_NAME})
        endif()
        include("${binary_dir}/tests.cmake")
endfunction()
