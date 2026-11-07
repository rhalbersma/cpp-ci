#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Measure what each public header costs to include, and compare two trees.

Each header matching the glob is compiled on its own, as a translation unit
holding nothing but its #include, under Clang with -ftime-trace. Three counts
are taken per header, and they are deterministic: the same tree and compiler
give the same numbers on every run, so a threshold on them cannot flap.

  lines      the preprocessed translation unit's length, -E -P
  classes    class template instantiations, Clang's InstantiateClass count
  functions  function template instantiations, its InstantiateFunction count

The frontend's wall time is reported beside them, the least of `repeat` runs,
for information only: on a shared runner it moves by a tenth from run to run.

Usage:

  budget.py measure --root DIR [--glob G] [--cxx clang++] [--flag F]... [--repeat N] --out FILE
  budget.py report HEAD
  budget.py compare BASE HEAD [--threshold PERCENT] [--min-delta N]

`measure` writes JSON keyed by header path relative to the root. `report`
prints one tree's table, costliest header first, and never fails. `compare`
prints a Markdown table of the headers whose counts changed, appends it to
GITHUB_STEP_SUMMARY on a runner, and exits 1 when any count grew by more than
the threshold, ignoring growth of fewer than min-delta. A header new in HEAD is
listed and never fails; a header gone from HEAD is listed as removed.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

COUNTS = ("lines", "classes", "functions")


def headers(root: pathlib.Path, pattern: str) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())


def trace_counts(trace: dict) -> tuple[dict[str, int], float]:
    """InstantiateClass and InstantiateFunction counts, and frontend seconds."""
    totals = {e["name"]: e for e in trace["traceEvents"] if e.get("name", "").startswith("Total ")}

    def count(name: str) -> int:
        event = totals.get(f"Total {name}")
        return int(event["args"]["count"]) if event else 0

    frontend = totals.get("Total Frontend")
    seconds = frontend["dur"] / 1e6 if frontend else 0.0
    return {"classes": count("InstantiateClass"), "functions": count("InstantiateFunction")}, seconds


def measure_one(header: str, cxx: str, flags: list[str], repeat: int, work: pathlib.Path) -> dict:
    source = work / "tu.cpp"
    source.write_text(f"#include <{header}>\n")
    preprocessed = subprocess.run([cxx, *flags, "-E", "-P", str(source)], capture_output=True, text=True)
    if preprocessed.returncode != 0:
        return {"error": preprocessed.stderr.strip().splitlines()[-1] if preprocessed.stderr.strip() else "preprocessing failed"}
    result: dict = {"lines": preprocessed.stdout.count("\n")}
    best = None
    for _ in range(repeat):
        # -fsyntax-only stops before the trace is written, so the unit is
        # compiled to an object that is thrown away.
        compiled = subprocess.run(
            [cxx, *flags, "-c", str(source), "-o", str(work / "tu.o"), "-ftime-trace", "-ftime-trace-granularity=500"],
            capture_output=True, text=True,
        )
        if compiled.returncode != 0:
            return {"error": compiled.stderr.strip().splitlines()[-1] if compiled.stderr.strip() else "compilation failed"}
        counts, seconds = trace_counts(json.loads((work / "tu.json").read_text()))
        result.update(counts)
        best = seconds if best is None else min(best, seconds)
    result["seconds"] = round(best or 0.0, 3)
    return result


def measure(root: pathlib.Path, pattern: str, cxx: str, flags: list[str], repeat: int) -> dict[str, dict]:
    found = headers(root, pattern)
    if not found:
        raise SystemExit(f"::error::no header under {root} matches {pattern}")
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        return {h: measure_one(h, cxx, [*flags, f"-I{root}"], repeat, work) for h in found}


def change(base: int, head: int, percent: float) -> str:
    return f"{base:,} → {head:,} (" + ("from none" if base == 0 else f"{percent:+.1f}%") + ")"


def growth(base: int, head: int) -> float:
    return float("inf") if base == 0 and head > 0 else (head - base) / base * 100 if base else 0.0


def compare(base: dict[str, dict], head: dict[str, dict], threshold: float, min_delta: int) -> tuple[str, list[str]]:
    rows = []
    over = []
    for header in sorted(set(base) | set(head)):
        before, after = base.get(header), head.get(header)
        if after is None:
            rows.append(f"| `{header}` | removed | | | |")
            continue
        if "error" in after:
            rows.append(f"| `{header}` | does not compile: {after['error']} | | | |")
            over.append(f"{header}: does not compile")
            continue
        if before is None or "error" in before:
            cells = " | ".join(f"{after[c]:,}" for c in COUNTS)
            rows.append(f"| `{header}` | new | {cells} | {after['seconds']:.2f} s |")
            continue
        changed = [c for c in COUNTS if after[c] != before[c]]
        if not changed:
            continue
        cells = []
        for c in COUNTS:
            if after[c] == before[c]:
                cells.append(f"{after[c]:,}")
                continue
            percent = growth(before[c], after[c])
            mark = ""
            if after[c] - before[c] >= min_delta and percent > threshold:
                mark = " ⚠"
                over.append(f"{header}: {c} {change(before[c], after[c], percent)}")
            cells.append(f"{change(before[c], after[c], percent)}{mark}")
        rows.append(f"| `{header}` | changed | {' | '.join(cells)} | {before['seconds']:.2f} → {after['seconds']:.2f} s |")

    lines = ["## Compile budget", ""]
    if not rows:
        lines.append(f"No header's counts changed, across {len(head)} header{'' if len(head) == 1 else 's'}.")
    else:
        lines += ["| Header | | Preprocessed lines | Class instantiations | Function instantiations | Frontend |",
                  "| :----- | :- | -----------------: | -------------------: | ----------------------: | -------: |"]
        lines += rows
        lines.append("")
        lines.append(f"⚠ marks growth above {threshold:g}% and of at least {min_delta:,}; frontend time is informational.")
    return "\n".join(lines) + "\n", over


def report(head: dict[str, dict]) -> str:
    lines = ["## Compile budget", "",
             "| Header | Preprocessed lines | Class instantiations | Function instantiations | Frontend |",
             "| :----- | -----------------: | -------------------: | ----------------------: | -------: |"]
    measured = {h: r for h, r in head.items() if "error" not in r}
    for header, r in sorted(measured.items(), key=lambda item: (-item[1]["lines"], item[0])):
        lines.append(f"| `{header}` | {r['lines']:,} | {r['classes']:,} | {r['functions']:,} | {r['seconds']:.2f} s |")
    for header in sorted(set(head) - set(measured)):
        lines.append(f"| `{header}` | does not compile: {head[header]['error']} | | | |")
    return "\n".join(lines) + "\n"


def emit(text: str) -> None:
    print(text)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(text)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--root", required=True, type=pathlib.Path)
    m.add_argument("--glob", default="**/*.hpp")
    m.add_argument("--cxx", default="clang++")
    m.add_argument("--flag", action="append", default=[])
    m.add_argument("--repeat", type=int, default=3)
    m.add_argument("--out", required=True, type=pathlib.Path)
    r = sub.add_parser("report")
    r.add_argument("head", type=pathlib.Path)
    c = sub.add_parser("compare")
    c.add_argument("base", type=pathlib.Path)
    c.add_argument("head", type=pathlib.Path)
    c.add_argument("--threshold", type=float, default=5.0)
    c.add_argument("--min-delta", type=int, default=1)
    args = parser.parse_args(argv)

    if args.command == "measure":
        result = measure(args.root.resolve(), args.glob, args.cxx, args.flag, max(1, args.repeat))
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        broken = [h for h, r in result.items() if "error" in r]
        for h in broken:
            print(f"::error::{h} does not compile on its own: {result[h]['error']}")
        return 1 if broken else 0

    if args.command == "report":
        emit(report(json.loads(args.head.read_text())))
        return 0

    text, over = compare(json.loads(args.base.read_text()), json.loads(args.head.read_text()), args.threshold, args.min_delta)
    emit(text)
    for item in over:
        print(f"::error::{item}")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
