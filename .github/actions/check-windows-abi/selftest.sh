#!/usr/bin/env bash
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

# The checker parses disassembly text, so it must be proven against the exact
# objdump on this runner: a format it does not recognise makes it silently
# match nothing and pass everything, which is worse than not running it.
set -uo pipefail
OBJDUMP="${1:-objdump}"; AS="${2:-cc}"
here=$(cd "$(dirname "$0")" && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
"$AS" -c -o "$tmp/cases.o" "$here/cases.s" || { echo "selftest: cannot assemble fixture"; exit 2; }
out=$("$here/check_windows_abi.py" --objdump "$OBJDUMP" "$tmp/cases.o" 2>/dev/null)
flagged=$(printf '%s\n' "$out" | sed -n 's/^.*cases\.o: \([a-z0-9_]*\):.*/\1/p' | sort -u | paste -sd, -)
expected='bad_no_realign,bad_zmm_under_32_realign'
if [ "$flagged" = "$expected" ]; then
  echo "selftest OK ($OBJDUMP): flagged exactly [$expected]"
else
  echo "selftest FAILED ($OBJDUMP)"; echo "  expected: $expected"; echo "  actual:   $flagged"; exit 1
fi
