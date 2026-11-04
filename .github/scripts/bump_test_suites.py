#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Move each standard library test suite's pin to the newest release of its series.

toolchains.json pins, on a rung, the tag of the suite that describes that rung's
standard library: releases/gcc-15.2.0 for GCC 15, llvmorg-22.1.8 for Clang 22,
vs-2022-17.14 for VS 2022. A pin moves within its series only, so GCC 15 stays on
GCC 15's releases and a rung bump still moves its suite in the same change. A
release candidate is no release, and a rung pinned to a commit, as a development
rung is, has no series to follow and is left as it is.

    .github/scripts/bump_test_suites.py            # rewrite toolchains.json in place
    .github/scripts/bump_test_suites.py --dry-run  # print the moves only

Each move is printed as "<family> <rung>: <old> -> <new>". With --summary <file>,
the same lines are written there as a Markdown list, for a pull request's body.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TABLE = ROOT / ".github" / "actions" / "toolchain" / "toolchains.json"
sys.path.insert(0, str(ROOT / "cmake"))
from std_test_suite import SUITES  # noqa: E402

# A tag is a prefix, its series number, and the dotted rest: releases/gcc-|15|.2.0, vs-2022-|17|.14.
SHAPE = re.compile(r"^(?P<prefix>.*?)(?P<series>\d+)(?P<rest>(?:\.\d+)*)$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")


def numbers(tag: str) -> tuple[int, ...]:
    shape = SHAPE.match(tag)
    assert shape, tag
    return (int(shape["series"]), *(int(part) for part in shape["rest"].split(".")[1:]))


def newest(pin: str, tags: list[str]) -> str:
    """The newest release among tags in pin's series, or pin itself where none is newer."""
    shape = SHAPE.match(pin)
    if not shape:
        return pin
    release = re.compile(re.escape(shape["prefix"] + shape["series"]) + r"(?:\.\d+)*")
    candidates = [tag for tag in tags if release.fullmatch(tag)]
    return max([pin, *candidates], key=numbers)


def remote_tags(repository: str, pin: str) -> list[str]:
    """Every tag of repository that starts as pin's series does, without fetching any object."""
    shape = SHAPE.match(pin)
    assert shape, pin
    pattern = f"refs/tags/{shape['prefix']}{shape['series']}*"
    out = subprocess.run(["git", "ls-remote", "--tags", "--refs", repository, pattern], capture_output=True, text=True, check=True)
    return [line.split("\t", 1)[1].removeprefix("refs/tags/") for line in out.stdout.splitlines() if "\t" in line]


def moves(table: dict, tags_of) -> list[tuple[str, str, str, str]]:
    """(family, rung, old, new) for each pin a newer release in its series replaces."""
    repositories = {suite.toolchain: suite.repository for suite in SUITES.values()}
    result = []
    for family, rungs in table.items():
        if family not in repositories:
            continue
        for rung, entry in rungs.items():
            pin = entry.get("test_suite") if isinstance(entry, dict) else None
            if not pin or COMMIT.fullmatch(pin) or not SHAPE.match(pin):
                continue
            new = newest(pin, tags_of(repositories[family], pin))
            if new != pin:
                result.append((family, rung, pin, new))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the moves without rewriting toolchains.json")
    parser.add_argument("--summary", type=pathlib.Path, help="also write the moves here as a Markdown list")
    args = parser.parse_args()

    text = TABLE.read_text()
    found = moves(json.loads(text), remote_tags)
    for family, rung, old, new in found:
        print(f"{family} {rung}: {old} -> {new}")
        # The pin's own line, so the file keeps its layout and every other line its bytes.
        line = f'"test_suite": "{old}"'
        if text.count(line) != 1:
            raise SystemExit(f"{TABLE.name}: expected one {line}, found {text.count(line)}")
        text = text.replace(line, f'"test_suite": "{new}"')
    if args.summary:
        args.summary.write_text("".join(f"- `{family}` {rung}: `{old}` → `{new}`\n" for family, rung, old, new in found))
    if found and not args.dry_run:
        TABLE.write_text(text)
    if not found:
        print("every test_suite pin is the newest release of its series")
    return 0


if __name__ == "__main__":
    sys.exit(main())
