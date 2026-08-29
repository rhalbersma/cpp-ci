#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

        .text
        .globl  bad_no_realign
bad_no_realign:
        pushq   %rbp
        movq    %rsp, %rbp
        subq    $128, %rsp
        vmovdqa %ymm0, -0x50(%rbp)
        vmovdqa -0x50(%rbp), %ymm0
        leave
        ret

        .globl  good_realigned_32
good_realigned_32:
        pushq   %rbp
        movq    %rsp, %rbp
        andq    $-32, %rsp
        subq    $128, %rsp
        vmovdqa %ymm0, 0x20(%rsp)
        leave
        ret

        .globl  good_unaligned_move
good_unaligned_move:
        pushq   %rbp
        movq    %rsp, %rbp
        vmovdqu %ymm0, -0x50(%rbp)
        leave
        ret

        .globl  good_xmm_aligned
good_xmm_aligned:
        pushq   %rbp
        movq    %rsp, %rbp
        vmovdqa %xmm0, -0x30(%rbp)
        leave
        ret

        .globl  bad_zmm_under_32_realign
bad_zmm_under_32_realign:
        pushq   %rbp
        movq    %rsp, %rbp
        andq    $-32, %rsp
        vmovdqa64 %zmm0, 0x40(%rsp)
        leave
        ret

        .globl  good_zmm_realigned_64
good_zmm_realigned_64:
        pushq   %rbp
        movq    %rsp, %rbp
        andq    $-64, %rsp
        vmovdqa64 %zmm0, 0x40(%rsp)
        leave
        ret

        .globl  good_aligned_not_stack
good_aligned_not_stack:
        vmovdqa %ymm0, (%rdi)
        ret
