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
#                  [RUNG <stable|qualification|development>]
#                  [TAG <tag>])
#
# <suite> is libcxx, libstdcxx or msvc-stl. Each runnable test becomes its own
# executable and CTest test, labelled std-test-suite and <suite>; a compile
# test becomes an object library. A test the suite or the mapping expects to
# fail at run must exit non-zero or crash; one expected to fail at compile is
# left out of all and registered as a build of its target that must fail, so
# an expected failure that starts passing turns red rather than staying
# excused. The target std-test-suite-<suite> builds every test that is meant
# to build.
#
# Needs git and a Python of 3.11 or later, for tomllib. A consumer reaches this
# module through FetchContent of this repository at the commit its workflows
# pin, behind an option that is off by default.

include_guard(GLOBAL)

set(STD_TEST_SUITE_SCRIPT "${CMAKE_CURRENT_LIST_DIR}/std_test_suite.py")
set(STD_TEST_SUITE_EXPECT_FAILURE "${CMAKE_CURRENT_LIST_DIR}/std_test_suite_expect_failure.cmake")

function(std_test_suite_add name)
        cmake_parse_arguments(PARSE_ARGV 1 arg "" "SUITE;KIND;SOURCE;EXPECT" "INCLUDE_DIRS;OPTIONS")
        string(REPLACE "-" "_" suite_var "${arg_SUITE}")
        if(arg_KIND STREQUAL "compile")
                add_library(${name} OBJECT "${arg_SOURCE}")
        else()
                add_executable(${name} "${arg_SOURCE}")
        endif()
        target_include_directories(${name} PRIVATE ${arg_INCLUDE_DIRS} ${STD_TEST_SUITE_INCLUDE_DIRS_${suite_var}})
        target_link_libraries(${name} PRIVATE ${STD_TEST_SUITE_LINK_LIBRARIES})
        target_compile_options(${name} PRIVATE ${arg_OPTIONS})
        set(labels std-test-suite ${arg_SUITE})
        if(NOT arg_EXPECT STREQUAL "compile")
                add_dependencies(std-test-suite-${arg_SUITE} ${name})
        endif()
        if(arg_EXPECT STREQUAL "compile")
                set_target_properties(${name} PROPERTIES EXCLUDE_FROM_ALL TRUE)
                add_test(NAME ${name} COMMAND ${CMAKE_COMMAND} --build ${CMAKE_BINARY_DIR} --target ${name} --config $<CONFIG>)
                # Builds of a shared tree do not run side by side.
                set_tests_properties(${name} PROPERTIES WILL_FAIL TRUE LABELS "${labels}" RESOURCE_LOCK std-test-suite-build)
        elseif(arg_KIND STREQUAL "run" AND arg_EXPECT STREQUAL "run")
                # WILL_FAIL would count a failed assert's abort as a failure, not the one expected.
                add_test(NAME ${name} COMMAND ${CMAKE_COMMAND} -DTEST=$<TARGET_FILE:${name}> -P ${STD_TEST_SUITE_EXPECT_FAILURE})
                set_tests_properties(${name} PROPERTIES LABELS "${labels}")
        elseif(arg_KIND STREQUAL "run")
                add_test(NAME ${name} COMMAND ${name})
                set_tests_properties(${name} PROPERTIES LABELS "${labels}")
        endif()
endfunction()

function(std_test_suite suite)
        cmake_parse_arguments(PARSE_ARGV 1 arg "" "RUNG;TAG" "MAPPING;LINK_LIBRARIES")
        if(NOT arg_MAPPING)
                message(FATAL_ERROR "std_test_suite(${suite}): MAPPING names no file")
        endif()
        if(NOT arg_RUNG)
                set(arg_RUNG stable)
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

        set(std ${CMAKE_CXX_STANDARD})
        if(NOT std)
                message(FATAL_ERROR "std_test_suite(${suite}): set CMAKE_CXX_STANDARD, the dialect the suites' markers are read against")
        endif()

        set(flags "${CMAKE_CXX_FLAGS}")
        set(extra)
        if(flags MATCHES "-fno-exceptions")
                list(APPEND extra --no-exceptions)
        endif()
        if(flags MATCHES "-fno-rtti|/GR-")
                list(APPEND extra --no-rtti)
        endif()
        if(arg_TAG)
                list(APPEND extra --tag ${arg_TAG})
        endif()
        set(mappings)
        foreach(file IN LISTS arg_MAPPING)
                get_filename_component(file "${file}" ABSOLUTE)
                list(APPEND mappings --mapping "${file}")
        endforeach()

        set(binary_dir "${CMAKE_CURRENT_BINARY_DIR}/std-test-suite/${suite}")
        execute_process(
                COMMAND ${Python3_EXECUTABLE} -I "${STD_TEST_SUITE_SCRIPT}"
                        --suite ${suite} ${mappings}
                        --tree "${CMAKE_BINARY_DIR}/_deps/std-test-suite-${suite}"
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
        if(NOT TARGET std-test-suite-${suite})
                add_custom_target(std-test-suite-${suite})
        endif()
        include("${binary_dir}/tests.cmake")
endfunction()
