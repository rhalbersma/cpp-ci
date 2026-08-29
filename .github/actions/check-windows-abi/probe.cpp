//          Copyright Rein Halbersma 2026.
// Distributed under the Boost Software License, Version 1.0.
//    (See accompanying file LICENSE_1_0.txt or copy at
//          http://www.boost.org/LICENSE_1_0.txt)

// Shapes that put a vector wider than the Windows x64 ABI's 16-byte stack
// guarantee into a stack slot.
//
// Getting a wide vector into a *frame slot* is the whole trick, and the first
// version of this file did not manage it. Returning a 32-byte aggregate by
// value looks like the right shape and is not: the ABI returns it through a
// hidden pointer, so the wide store lands in the caller's buffer and the callee
// frame never holds it. That corpus reported plenty of wide registers and zero
// wide stack accesses on all three MinGW rungs - a green result over nothing.
//
// What works is making the value frame-resident: take its address, or keep it
// live across an opaque call so the register allocator has to spill it. Both
// are ordinary C++, which is the point. The bug lands wherever the compiler
// happened to spill, so the corpus does not need to resemble what broke - it
// needs to spill.
//
// `observe` is deliberately not defined here. An opaque call the compiler
// cannot see through is what forces the spill and stops it from folding the
// locals away.

#include <array>
#include <cstddef>
#include <cstdint>

using Block   = std::array<std::uint64_t, 4>;   // 32 bytes: one ymm
using Block64 = std::array<std::uint64_t, 8>;   // 64 bytes: one zmm

extern void observe(void const*);

// 1. An address-taken local array. The address escapes into `observe`, so the
//    array must live in the frame, and the loop over it vectorises.
std::uint64_t frame_resident(Block const* in)
{
        Block t[4];
        for (int i = 0; i < 4; ++i)
                for (std::size_t j = 0; j < 4; ++j)
                        t[i][j] = in[i][j] ^ in[3 - i][3 - j];
        observe(t);
        std::uint64_t s = 0;
        for (auto const& b : t)
                for (auto v : b)
                        s += v;
        return s;
}

// 2. Several wide values live across a call. Nothing takes their address; the
//    register allocator spills them because the call clobbers the registers.
//    This is the reload-spill path, which is where the fault was found.
std::uint64_t spill_across_call(Block const* in)
{
        Block a = in[0], b = in[1], c = in[2], d = in[3];
        observe(in);
        std::uint64_t s = 0;
        for (std::size_t j = 0; j < 4; ++j)
                s += a[j] + b[j] + c[j] + d[j];
        return s;
}

// 3. The same as (1) at 64 bytes, for a toolchain that reaches for zmm. A
//    frame realigned to 32 is not far enough for these, which the checker
//    treats as a finding rather than as covered.
std::uint64_t frame_resident_64(Block64 const* in)
{
        Block64 t[2];
        for (int i = 0; i < 2; ++i)
                for (std::size_t j = 0; j < 8; ++j)
                        t[i][j] = in[i][j] + in[1 - i][7 - j];
        observe(t);
        std::uint64_t s = 0;
        for (auto const& b : t)
                for (auto v : b)
                        s += v;
        return s;
}

// 4. Enough simultaneously live wide values to exceed the register file, so
//    the allocator spills even without a call in the way.
std::uint64_t register_pressure(Block const* in, std::size_t n)
{
        Block acc[20]{};
        for (std::size_t i = 0; i < n; ++i)
                for (std::size_t k = 0; k < 20; ++k)
                        for (std::size_t j = 0; j < 4; ++j)
                                acc[k][j] += in[(i + k) % 4][j] * (k + 1);
        observe(acc);
        std::uint64_t s = 0;
        for (auto const& b : acc)
                for (auto v : b)
                        s += v;
        return s;
}
