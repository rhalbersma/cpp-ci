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

Reads `objdump -d` (AT&T syntax) on stdin, or from named object files, or
from directories walked for object files - which is how it is pointed at a
project's own build tree.
"""
import argparse, os, re, subprocess, sys

OBJECT_SUFFIXES = ('.o', '.obj')

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
    stats = {'wide_uses': 0, 'wide_stack': 0,
             'aligned_wide_stack': 0, 'realigned_functions': 0}
    for line in disassembly.splitlines():
        header = FUNC.match(line.strip())
        if header:
            findings.extend((func, w, t) for w, t in pending if w > aligned_to)
            func, aligned_to, pending = header.group('name'), 0, []
            continue
        body = line.replace('\t', ' ')
        if WIDE.search(body):
            stats['wide_uses'] += 1
            # Wide traffic against a frame slot, aligned or not. This is the
            # liveness signal: a toolchain that renders these `vmovdqu` is
            # sound by construction, and one that renders the same slots
            # `vmovdqa` is the bug. Counting only the aligned ones cannot tell
            # "sound" from "never got a wide value onto the stack at all".
            if STACK.search(body):
                stats['wide_stack'] += 1
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


def expand(paths):
    """Resolve directories to the object files under them, so the checker can
    be pointed at a build tree. A synthetic corpus cannot be relied on to
    provoke this bug - it depends on how a compiler chose to spill in deeply
    inlined code - so scanning what a project actually built is the only way to
    ask the question about that project."""
    out = []
    for path in paths:
        if path == '-' or os.path.isfile(path):
            out.append(path)
        elif os.path.isdir(path):
            for root, _, files in os.walk(path):
                out.extend(os.path.join(root, f) for f in sorted(files)
                           if f.endswith(OBJECT_SUFFIXES))
        else:
            out.append(path)                # let objdump report it
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('objects', nargs='*',
                    help='Object files, or directories to walk for them. '
                         '"-" reads disassembly from stdin.')
    ap.add_argument('--objdump', default='objdump')
    ap.add_argument('--require-wide-stack', action='store_true',
                    help='Fail if nothing wider than 16 bytes ever reached a '
                         'stack slot. Wide registers alone are not enough: a '
                         'value returned through a hidden pointer is stored in '
                         'the caller\'s buffer and never occupies the callee '
                         'frame, so a pass over such code says nothing about '
                         'stack alignment.')
    args = ap.parse_args()

    findings = 0
    totals = {'wide_uses': 0, 'wide_stack': 0,
              'aligned_wide_stack': 0, 'realigned_functions': 0}
    sources = expand(args.objects) or ['-']
    for src in sources:
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
    print(f'objects scanned: {len(sources)}; '
          f'wide-register uses: {totals["wide_uses"]}; '
          f'wide stack accesses: {totals["wide_stack"]}; '
          f'of those aligned: {totals["aligned_wide_stack"]}; '
          f'functions that realigned: {totals["realigned_functions"]}')

    if findings:
        print(f'\n{findings} over-aligned stack access(es) not covered by a '
              f'stack realignment. These fault on Windows.', file=sys.stderr)
        return 1

    if args.require_wide_stack and totals['wide_stack'] == 0:
        print(f'\nNothing wider than 16 bytes reached a stack slot '
              f'({totals["wide_uses"]} wide-register uses, none against the '
              f'frame), so nothing here could have been misaligned and this run '
              f'proves nothing about the toolchain. Either the target has no '
              f'AVX - check what the vector flags resolved to - or the corpus '
              f'no longer spills on this compiler and needs a shape that does.',
              file=sys.stderr)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
