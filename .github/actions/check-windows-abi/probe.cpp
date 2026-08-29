//          Copyright Rein Halbersma 2026.
// Distributed under the Boost Software License, Version 1.0.
//    (See accompanying file LICENSE_1_0.txt or copy at
//          http://www.boost.org/LICENSE_1_0.txt)

// Shapes that provoke a compiler into putting a vector wider than the Windows
// x64 ABI's 16-byte stack guarantee into a stack slot. Nothing here is exotic:
// these are ordinary aggregate copies and locals, which is the point - the bug
// they detect lands in whatever function the compiler happened to spill in.

#include <array>
#include <cstddef>
#include <cstdint>

struct Bytes32 { std::uint64_t a, b, c, d; };
struct Bytes64 { std::uint64_t a, b, c, d, e, f, g, h; };

using Array32 = std::array<std::uint64_t, 4>;
using Array64 = std::array<std::uint64_t, 8>;

// 1. An explicitly over-aligned local. The most direct request for a stack
//    slot the ABI does not promise.
Bytes32 over_aligned_local(Bytes32 const& x)
{
        alignas(32) Bytes32 y = x;
        y.a += 1;
        return y;
}

// 2. A plain 32-byte aggregate copy, with no alignment request at all. This is
//    the shape that broke bit_set: a compiler may still choose a 32-byte move.
Bytes32 aggregate_copy(Bytes32 const& x)
{
        Bytes32 y = x;
        y.b += 1;
        return y;
}

// 3. The same through std::array, which is what a block-based container is.
Array32 array_copy(Array32 const& x)
{
        Array32 y = x;
        y[2] += 1;
        return y;
}

// 4. Several 32-byte values live at once, to force spills rather than letting
//    everything stay in registers.
Bytes32 spill_pressure(Bytes32 const& p, Bytes32 const& q, Bytes32 const& r, Bytes32 const& s)
{
        Bytes32 a = p, b = q, c = r, d = s;
        a.a += d.d; b.b += c.c; c.c += b.b; d.d += a.a;
        Bytes32 out{ a.a + b.a, b.b + c.b, c.c + d.c, d.d + a.d };
        return out;
}

// 5. 64-byte shapes, for a toolchain that reaches for zmm.
Bytes64 over_aligned_local_64(Bytes64 const& x)
{
        alignas(64) Bytes64 y = x;
        y.a += 1;
        return y;
}

Array64 array_copy_64(Array64 const& x)
{
        Array64 y = x;
        y[5] += 1;
        return y;
}
