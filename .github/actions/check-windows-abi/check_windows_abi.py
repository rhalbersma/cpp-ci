#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Fail on a stack access wider than the Windows x64 ABI's alignment guarantee.

The ABI promises a 16-byte aligned stack. An *aligned* vector move against a
32-byte ymm or 64-byte zmm register therefore only works if the function first
realigned the stack itself, with an `and $-32,%rsp` or `and $-64,%rsp`. GCC
does emit that realignment on Linux - and on Windows it emitted the aligned
move without it, which is a general protection fault the moment the slot lands
on a 16-but-not-32 boundary.

So the check is not "is there an aligned wide stack access" - correct code has
those - but "is there one in a function that never realigned far enough".

Reads `objdump -d` (AT&T syntax) on stdin or from named object files.
"""
import argparse, re, subprocess, sys

FUNC    = re.compile(r'^[0-9a-f]+ <(?P<name>.+)>:$')
# and $0xffffffffffffffe0,%rsp  /  and $-32,%rsp
# GNU objdump renders the mask two's complement (`$0xffffffffffffffe0`);
# llvm-objdump renders it signed (`$-0x20`). Accept both.
REALIGN = re.compile(r'\band[lq]?\s+\$(?P<imm>-?0x[0-9a-f]+|-?\d+),\s*%rsp\b')
ALIGNED = re.compile(
    r'\b(?P<op>vmov(?:dqa(?:32|64)?|aps|apd))\s+(?P<args>\S.*?)\s*(?:#.*)?$'
)
WIDE    = re.compile(r'%(?P<cls>[yz])mm\d+')
STACK   = re.compile(r'(?:-?0x[0-9a-f]+)?\((?:%rbp|%rsp)\)')

WIDTH = {'y': 32, 'z': 64}


def realign_width(imm):
    """Bytes a given `and` mask aligns the stack to, or 0 if it is not a mask."""
    negative = imm.startswith('-')
    digits = imm[1:] if negative else imm
    value = int(digits, 16) if digits.startswith('0x') else int(digits)
    if negative:
        value = -value
    elif value >= 1 << 63:              # two's complement, as GNU objdump prints it
        value -= 1 << 64
    if value >= 0:
        return 0
    magnitude = -value                  # -32 -> 32
    return magnitude if magnitude and (magnitude & (magnitude - 1)) == 0 else 0


def scan(disassembly):
    """Return (findings, stats). Stats matter as much as findings: a run that
    produced no wide vectors at all has proven nothing, and must not be
    reported as if the toolchain had passed something."""
    findings, func, aligned_to = [], '?', 0
    pending = []
    stats = {'wide_uses': 0, 'aligned_wide_stack': 0, 'realigned_functions': 0}
    for line in disassembly.splitlines():
        header = FUNC.match(line.strip())
        if header:
            findings.extend((func, w, t) for w, t in pending if w > aligned_to)
            func, aligned_to, pending = header.group('name'), 0, []
            continue
        body = line.replace('\t', ' ')
        if WIDE.search(body):
            stats['wide_uses'] += 1
        r = REALIGN.search(body)
        if r:
            width = realign_width(r.group('imm'))
            if width and not aligned_to:
                stats['realigned_functions'] += 1
            aligned_to = max(aligned_to, width)
            continue
        m = ALIGNED.search(body)
        if m:
            args = m.group('args')
            w = WIDE.search(args)
            if w and STACK.search(args):
                stats['aligned_wide_stack'] += 1
                pending.append((WIDTH[w.group('cls')], body.strip()))
    findings.extend((func, w, t) for w, t in pending if w > aligned_to)
    return findings, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('objects', nargs='*')
    ap.add_argument('--objdump', default='objdump')
    ap.add_argument('--require-wide', action='store_true',
                    help='Fail if the corpus produced no vector wider than 16 '
                         'bytes. Without one there is nothing to misalign, so a '
                         'pass would say nothing about the toolchain - only that '
                         'the compiler declined to vectorise.')
    args = ap.parse_args()

    findings = 0
    totals = {'wide_uses': 0, 'aligned_wide_stack': 0, 'realigned_functions': 0}
    for src in args.objects or ['-']:
        text = sys.stdin.read() if src == '-' else subprocess.run(
            [args.objdump, '-d', src], capture_output=True, text=True,
            check=True).stdout
        hits, stats = scan(text)
        for key in totals:
            totals[key] += stats[key]
        for func, width, insn in hits:
            print(f'{src}: {func}: needs {width}-byte alignment, stack '
                  f'guarantees 16 and this function did not realign:\n    {insn}')
            findings += 1

    # Always report what was seen. A silent pass cannot be told apart from a
    # pass over nothing, and the difference is the whole value of the check.
    print(f'wide-register uses: {totals["wide_uses"]}; '
          f'aligned wide stack accesses: {totals["aligned_wide_stack"]}; '
          f'functions that realigned: {totals["realigned_functions"]}')

    if findings:
        print(f'\n{findings} over-aligned stack access(es) not covered by a '
              f'stack realignment. These fault on Windows.', file=sys.stderr)
        return 1

    if args.require_wide and totals['wide_uses'] == 0:
        print('\nThe corpus produced no vector wider than 16 bytes, so nothing '
              'here could have been misaligned and this run proves nothing '
              'about the toolchain. Either the target has no AVX (check what '
              '-march=native resolved to) or the corpus no longer provokes this '
              'compiler and needs a shape that does.', file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
