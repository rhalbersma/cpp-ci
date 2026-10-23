#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Run a standard library's own test suite against a library that mirrors part of it.

libc++, libstdc++ and the MSVC STL each ship a test suite written against the
standard's paragraphs by people with no stake in the library under test. This
script fetches one of them at a pinned tag, reads its own markers to decide
which tests run and with which flags, rewrites a build-tree copy of each test
from the standard's spelling onto the library's, and writes a CMake file that
StdTestSuite.cmake includes to register every runnable test with CTest.

What is the consumer's and what is the suite's are kept apart:

  the suite's     where it lives, its pinned tag, its support headers, and how
                  its markers read; all of it here, written once
  the consumer's  the mapping: the directories to take, the support headers
                  to rewrite beside them, the spelling to rewrite, the cases
                  expected to fail by design, and the per-assertion
                  exceptions; TOML files in the consuming repository, passed
                  with --mapping

A mapping entry that has gone stale is an error, never a silent pass: an
expected failure or an exception naming a file the tree no longer has, a file
whose contents no longer hash to what the entry was written against, or a line
the file no longer carries. An upstream edit therefore brings its verdict back
for review instead of carrying the old one forward.

Every file under a taken directory lands in manifest.json with its hash and
what became of it, so a consumer can hold a classification table to the tree.
"""

from __future__ import annotations

import argparse
import dataclasses
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable

HERE = pathlib.Path(__file__).resolve().parent
TOOLCHAINS = HERE.parent / ".github" / "actions" / "toolchain" / "toolchains.json"


# ---------------------------------------------------------------------------
# What the test is compiled with
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Config:
    """The facts every suite's markers are evaluated against.

    These come from CMake, which already knows them, rather than from probing
    the compiler again: the compiler family and version, the standard library
    it builds against, the language standard as its two-digit year, the
    target triple, and whether exceptions and RTTI are on.
    """

    compiler: str            # gcc, clang, apple-clang or msvc
    version: str             # dotted, e.g. 15.2.0
    stdlib: str              # libstdc++, libc++ or msvc
    std: int                 # 23, 26, ...
    target: str              # e.g. x86_64-pc-linux-gnu
    exceptions: bool = True
    rtti: bool = True

    @property
    def gcc_style(self) -> bool:
        return self.compiler != "msvc"


STANDARDS = [3, 11, 14, 17, 20, 23, 26, 29]


def std_rank(year: int) -> int:
    """Order two-digit standard years, where 03 precedes 11 but 98 precedes both."""
    return 0 if year == 98 else STANDARDS.index(year) + 1 if year in STANDARDS else year


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Test:
    """One file under a taken directory, and what became of it.

    kind is run (build and run), build (build and link, never run), compile
    (an object file, no main required) or skip. A skipped file carries the
    reason; one the consumer expects to fail carries the stage it fails at and
    the paragraph the library departs from.
    """

    path: str
    sha256: str
    kind: str
    reason: str = ""
    options: list[str] = dataclasses.field(default_factory=list)
    expect: str = ""          # compile or run, for an expected failure
    paragraph: str = ""
    exceptions: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# libc++: lit's markers and boolean expressions
# ---------------------------------------------------------------------------

LIT_TOKEN = re.compile(r"\s*([()]|&&|\|\||!|(?:[-+=._a-zA-Z0-9]+|\{\{.+?\}\})+)")


def lit_eval(expression: str, features: set[str]) -> bool:
    """Evaluate one of lit's boolean expressions over a set of features.

    The grammar is lit's own: identifiers joined by &&, || and !, with
    parentheses, true and false; an identifier containing {{...}} is a regular
    expression, every other character of it literal, matched in full against
    each feature. A feature this adapter does not define is false.
    """
    tokens = []
    pos = 0
    expression = expression.strip()
    while pos < len(expression):
        m = LIT_TOKEN.match(expression, pos)
        if not m:
            raise ValueError(f"cannot parse lit expression {expression!r} at {pos}")
        tokens.append(m.group(1))
        pos = m.end()
        while pos < len(expression) and expression[pos].isspace():
            pos += 1
    tokens.append("")
    index = 0

    def peek() -> str:
        return tokens[index]

    def take() -> str:
        nonlocal index
        index += 1
        return tokens[index - 1]

    def identifier(name: str) -> bool:
        if name == "true":
            return True
        if name == "false":
            return False
        if "{{" not in name:
            return name in features
        pattern = "".join(
            part[2:-2] if part.startswith("{{") else re.escape(part)
            for part in re.split(r"(\{\{.+?\}\})", name) if part
        )
        regex = re.compile(pattern)
        return any(regex.fullmatch(f) for f in features)

    def or_expr() -> bool:
        value = and_expr()
        while peek() == "||":
            take()
            rhs = and_expr()
            value = value or rhs
        return value

    def and_expr() -> bool:
        value = not_expr()
        while peek() == "&&":
            take()
            rhs = not_expr()
            value = value and rhs
        return value

    def not_expr() -> bool:
        token = take()
        if token == "!":
            return not not_expr()
        if token == "(":
            value = or_expr()
            if take() != ")":
                raise ValueError(f"unbalanced parentheses in {expression!r}")
            return value
        if token in ("", ")", "&&", "||"):
            raise ValueError(f"unexpected {token or 'end'!r} in {expression!r}")
        return identifier(token)

    value = or_expr()
    if peek() != "":
        raise ValueError(f"trailing {peek()!r} in {expression!r}")
    return value


def lit_features(config: Config) -> set[str]:
    """The lit features libc++'s own configuration would define for this build.

    Only the ones a test under libcxx/test/std/ conditions on: the dialect,
    the compiler with each prefix of its version, the standard library, the
    target, and the flag families. Anything else reads as absent.
    """
    year = config.std
    features = {f"c++{year:02d}"}
    name = config.compiler
    parts = config.version.split(".")
    features.add(name)
    for n in range(1, len(parts) + 1):
        features.add(f"{name}-{'.'.join(parts[:n])}")
    features.add(f"target={config.target}")
    if config.stdlib == "libc++":
        features |= {"stdlib=libc++", "stdlib=llvm-libc++"}
    elif config.stdlib == "libstdc++":
        features.add("stdlib=libstdc++")
    elif config.stdlib == "msvc":
        features.add("stdlib=msvc")
    if not config.exceptions:
        features.add("no-exceptions")
    if not config.rtti:
        features.add("no-rtti")
    if config.gcc_style:
        features.add("gcc-style-warnings")
    else:
        features.add("cl-style-warnings")
    if config.compiler in ("clang", "apple-clang"):
        features.add("has-fconstexpr-steps")
    if config.compiler == "gcc":
        features.add("has-fconstexpr-ops-limit")
    return features


LIT_MARKER = re.compile(r"^\s*//\s*(REQUIRES|UNSUPPORTED|XFAIL|ADDITIONAL_COMPILE_FLAGS|FILE_DEPENDENCIES|RUN|MODULE_DEPENDENCIES)(?:\((.*?)\))?\s*:(.*)$")


def libcxx_classify(path: str, text: str, config: Config) -> tuple[str, str, list[str], str]:
    """Kind, reason, options and lit's own expected-failure verdict for one libc++ file."""
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".compile.pass.cpp"):
        kind = "compile"
    elif name.endswith(".link.pass.cpp"):
        kind = "build"
    elif name.endswith(".pass.cpp"):
        kind = "run"
    elif name.endswith(".verify.cpp") or name.endswith(".compile.fail.cpp"):
        return "skip", "a test of diagnostics, run in a second pass", [], ""
    elif name.endswith(".cpp"):
        return "skip", "not a test lit runs on its own", [], ""
    else:
        return "skip", "not a test source", [], ""

    features = lit_features(config)
    options: list[str] = []
    xfail = ""
    for line in text.splitlines():
        m = LIT_MARKER.match(line)
        if not m:
            continue
        keyword, condition, value = m.group(1), m.group(2), m.group(3).strip()
        if keyword == "RUN":
            return "skip", "custom RUN lines, which this adapter does not interpret", [], ""
        if keyword in ("FILE_DEPENDENCIES", "MODULE_DEPENDENCIES"):
            return "skip", f"{keyword}, which this adapter does not stage", [], ""
        clauses = [c for c in (c.strip() for c in value.split(",")) if c]
        if keyword == "REQUIRES":
            for clause in clauses:
                if not lit_eval(clause, features):
                    return "skip", f"REQUIRES: {clause}", [], ""
        elif keyword == "UNSUPPORTED":
            for clause in clauses:
                if clause == "*" or lit_eval(clause, features):
                    return "skip", f"UNSUPPORTED: {clause}", [], ""
        elif keyword == "XFAIL":
            for clause in clauses:
                if clause == "*" or lit_eval(clause, features):
                    xfail = f"XFAIL: {clause}"
        elif keyword == "ADDITIONAL_COMPILE_FLAGS":
            if condition and not lit_eval(condition, features):
                continue
            if "%{" in value:
                return "skip", f"ADDITIONAL_COMPILE_FLAGS with a lit substitution: {value}", [], ""
            options += shlex.split(value)
    return kind, "", options, xfail


# ---------------------------------------------------------------------------
# libstdc++: DejaGnu's directives
# ---------------------------------------------------------------------------

DG_DIRECTIVE = re.compile(r"\{\s*(dg-[a-z0-9-]+)((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}")

# Effective targets libstdc++'s tests require that a build of a C++ library
# against any conforming standard library satisfies. Anything else reads as
# absent, and a test requiring it is skipped with that name as the reason.
DG_SATISFIED = {"hosted", "cxx11_abi", "std_allocator", "cstdint", "fileio", "string_conversions", "time", "c99_math", "random_tr1", "gthreads", "pthread", "cxx11_atomic", "atomic_wait", "threads"}


def dg_words(text: str) -> list[str]:
    """Split a DejaGnu argument list into words, keeping each braced or quoted group whole."""
    words = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
        elif text[pos] == "{":
            depth, end = 0, pos
            while end < len(text):
                depth += {"{": 1, "}": -1}.get(text[end], 0)
                end += 1
                if depth == 0:
                    break
            words.append(text[pos:end])
            pos = end
        elif text[pos] == '"':
            end = text.index('"', pos + 1) if '"' in text[pos + 1:] else len(text)
            words.append(text[pos:end + 1])
            pos = end + 1
        else:
            end = pos
            while end < len(text) and not text[end].isspace():
                end += 1
            words.append(text[pos:end])
            pos = end
    return words


def unbrace(word: str) -> str:
    word = word.strip()
    if (word.startswith("{") and word.endswith("}")) or (word.startswith('"') and word.endswith('"')):
        return word[1:-1].strip()
    return word


def dg_effective(name: str, config: Config) -> bool:
    """One of libstdc++'s effective-target keywords, for this build."""
    m = re.fullmatch(r"c\+\+(\d\d)(_only|_down)?", name)
    if m:
        year, suffix = int(m.group(1)), m.group(2)
        if suffix == "_only":
            return year == config.std
        if suffix == "_down":
            return std_rank(config.std) <= std_rank(year)
        return std_rank(config.std) >= std_rank(year)
    if name == "exceptions_enabled":
        return config.exceptions
    if name == "rtti_enabled":
        return config.rtti
    return name in DG_SATISFIED


def dg_selector(selector: str, config: Config) -> bool:
    """A DejaGnu target selector: triple globs and effective targets, with &&, || and !."""
    tokens = re.findall(r"\(|\)|&&|\|\||!|[^\s()!&|]+", unbrace(selector))
    tokens.append("")
    index = 0

    def take() -> str:
        nonlocal index
        index += 1
        return tokens[index - 1]

    def atom(word: str) -> bool:
        if "-" in word and ("*" in word or word.count("-") >= 2):
            return fnmatch.fnmatchcase(config.target, word)
        return dg_effective(word, config)

    def or_expr() -> bool:
        value = and_expr()
        while tokens[index] in ("||",):
            take()
            rhs = and_expr()
            value = value or rhs
        return value

    def and_expr() -> bool:
        value = not_expr()
        while tokens[index] == "&&":
            take()
            rhs = not_expr()
            value = value and rhs
        return value

    def not_expr() -> bool:
        token = take()
        if token == "!":
            return not not_expr()
        if token == "(":
            value = or_expr()
            take()
            return value
        return atom(token)

    # A bare list of words is an implicit "or": { *-*-linux* *-*-darwin* }.
    value = False
    while tokens[index] != "":
        value = or_expr() or value
    return value


def dg_selector_clause(words: list[str], config: Config) -> tuple[bool, bool]:
    """Whether a { target ... } clause holds, and whether an { xfail ... } clause does."""
    if len(words) == 1 and words[0].startswith("{"):
        words = dg_words(unbrace(words[0]))
    target, xfail = True, False
    i = 0
    while i < len(words):
        if words[i] == "target" and i + 1 < len(words):
            target = target and dg_selector(words[i + 1], config)
            i += 2
        elif words[i] == "xfail" and i + 1 < len(words):
            xfail = xfail or dg_selector(words[i + 1], config)
            i += 2
        else:
            i += 1
    return target, xfail


def libstdcxx_classify(path: str, text: str, config: Config) -> tuple[str, str, list[str], str]:
    """Kind, reason, options and DejaGnu's own expected-failure verdict for one libstdc++ file."""
    if not path.endswith(".cc"):
        return "skip", "not a test source", [], ""
    if config.stdlib != "libstdc++":
        return "skip", "libstdc++'s support headers include its internal headers, such as <bits/c++config.h>", [], ""

    kind, xfail = "run", ""
    options: list[str] = []
    skips: list[tuple[list[str], str]] = []
    run_xfails: list[tuple[list[str], str]] = []
    for m in DG_DIRECTIVE.finditer(text):
        directive, words = m.group(1), dg_words(m.group(2))
        if directive == "dg-do":
            action = words[0] if words else "run"
            holds, fails = dg_selector_clause(words[1:], config)
            if not holds:
                return "skip", f"dg-do {action} {' '.join(words[1:])}", [], ""
            kind = {"run": "run", "link": "build", "compile": "compile"}.get(action, "")
            if not kind:
                return "skip", f"dg-do {action}, which builds no test", [], ""
            if fails:
                xfail = f"dg-do {' '.join(words)}"
        elif directive in ("dg-error", "dg-excess-errors"):
            return "skip", "a test of diagnostics, run in a second pass", [], ""
        elif directive in ("dg-options", "dg-additional-options"):
            holds, _ = dg_selector_clause(words[1:], config)
            if holds and words:
                options += shlex.split(unbrace(words[0]))
        elif directive == "dg-require-effective-target":
            for word in words[:1]:
                if not dg_effective(unbrace(word), config):
                    return "skip", f"dg-require-effective-target {unbrace(word)}", [], ""
        elif directive.startswith("dg-require-"):
            feature = directive.removeprefix("dg-require-").replace("-", "_")
            if feature not in DG_SATISFIED:
                return "skip", directive, [], ""
        elif directive == "dg-skip-if":
            skips.append((words, m.group(0)))
        elif directive == "dg-xfail-run-if":
            run_xfails.append((words, m.group(0)))
        elif directive == "dg-add-options":
            if words and unbrace(words[0]) not in ("no_pch",):
                return "skip", f"dg-add-options {unbrace(words[0])}, which this adapter does not interpret", [], ""

    # The test's own -std= gives way to the consumer's standard, which is the
    # one the library under test requires; the target selectors above already
    # decided whether the test applies to it.
    options = [o for o in options if not o.startswith("-std=")]
    for words, directive in skips:
        if dg_options_selected(words, options, config):
            return "skip", directive, [], ""
    for words, directive in run_xfails:
        if kind == "run" and dg_options_selected(words, options, config):
            xfail = directive
    return kind, "", options, xfail


def dg_options_selected(words: list[str], options: list[str], config: Config) -> bool:
    """Whether a dg-skip-if or dg-xfail-run-if applies: its target selector holds, its include options are all given and its exclude options none."""
    selector = words[1] if len(words) > 1 else "{ *-*-* }"
    include = shlex.split(unbrace(words[2])) if len(words) > 2 else []
    exclude = shlex.split(unbrace(words[3])) if len(words) > 3 else []
    if not dg_selector(selector, config):
        return False
    if include and include != ["*"] and not all(o in options for o in include):
        return False
    return not any(o in options for o in exclude)


# ---------------------------------------------------------------------------
# MSVC STL: env.lst
# ---------------------------------------------------------------------------

def msvc_env(path: pathlib.Path, root: pathlib.Path) -> list[list[str]]:
    """The cross-list sections of an env.lst, includes expanded: one list of PM_CL strings per section.

    Lines carrying a PM_COMPILER (clang-cl) or a tag (ASAN, ...) are left out,
    since the leg this runs on supplies its own compiler and the sanitizers
    are another workflow's business.
    """
    sections: list[list[str]] = []
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("RUNALL_INCLUDE"):
            target = (path.parent / line.split(None, 1)[1].replace("\\", "/")).resolve()
            if not target.is_file() or root not in target.parents:
                raise FileNotFoundError(f"{path}: RUNALL_INCLUDE {target} is not in the fetched tree")
            sections += msvc_env(target, root)
        elif line == "RUNALL_CROSSLIST":
            sections.append([])
        else:
            if "PM_COMPILER" in line or not line.startswith(("PM_CL", "*")):
                continue
            m = re.search(r'PM_CL="([^"]*)"', line)
            if m:
                if not sections:
                    sections.append([])
                sections[-1].append(m.group(1))
    return sections


MSVC_KEPT = re.compile(r"^/(D|U|EH|Zc:|permissive|GR|w[1-4]?\d|wd\d|utf-8)")


def msvc_options(sections: list[list[str]], config: Config) -> list[str]:
    """One configuration of the cross product: the first line of each section, preferring /std:c++latest."""
    chosen: list[str] = []
    for section in sections:
        if not section:
            continue
        pick = next((s for s in section if "/std:c++latest" in s), section[0])
        chosen += pick.split()
    kept = [o for o in chosen if MSVC_KEPT.match(o) and not o.startswith("/std:")]
    if config.gcc_style:
        return [("-" + o[1:]) for o in kept if o.startswith(("/D", "/U"))]
    return kept


def msvc_classify(path: str, text: str, config: Config, tree: pathlib.Path) -> tuple[str, str, list[str], str]:
    """Kind, reason and options for one MSVC STL file."""
    name = path.rsplit("/", 1)[-1]
    if name == "test.cpp":
        kind = "run"
    elif name == "test.compile.pass.cpp":
        kind = "compile"
    else:
        return "skip", "not a test source", [], ""
    env = (tree / path).parent / "env.lst"
    if not env.is_file():
        return "skip", "no env.lst beside it", [], ""
    return kind, "", msvc_options(msvc_env(env, tree.resolve()), config), ""


# ---------------------------------------------------------------------------
# The suites
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Suite:
    """Where a suite lives and how its files read.

    toolchain is the family in toolchains.json whose rung pins the suite's
    tag; root is the prefix every test path shares, which test names leave
    out; support is what goes on the include path; fetch is what is checked
    out alongside whatever the consumer takes: the support headers, and every
    header in the test tree, which a test reaches by a relative path such as
    "../insert_range_sequence_containers.h" from directories it does not take.
    """

    repository: str
    toolchain: str
    root: str
    support: tuple[str, ...]
    fetch: tuple[str, ...]
    classify: Callable[..., tuple[str, str, list[str], str]]


SUITES = {
    "libcxx": Suite(
        repository="https://github.com/llvm/llvm-project",
        toolchain="clang",
        root="libcxx/test/std/",
        support=("libcxx/test/support",),
        fetch=("/libcxx/test/support/", "/libcxx/test/std/**/*.h"),
        classify=libcxx_classify,
    ),
    "libstdcxx": Suite(
        repository="https://github.com/gcc-mirror/gcc",
        toolchain="gcc",
        root="libstdc++-v3/testsuite/",
        support=("libstdc++-v3/testsuite/util",),
        fetch=("/libstdc++-v3/testsuite/util/", "/libstdc++-v3/testsuite/**/*.h"),
        classify=libstdcxx_classify,
    ),
    "msvc-stl": Suite(
        repository="https://github.com/microsoft/STL",
        toolchain="msvc",
        root="tests/std/tests/",
        support=("tests/std/include",),
        fetch=("/tests/std/include/", "/tests/**/*.lst", "/tests/std/tests/**/*.hpp"),
        classify=msvc_classify,
    ),
}


def pinned_tag(suite: Suite, rung: str) -> str:
    """The suite's tag on this rung, from the toolchain table the ladders read."""
    table = json.loads(TOOLCHAINS.read_text())
    entry = table.get(suite.toolchain, {}).get(rung)
    if not entry or "test_suite" not in entry:
        raise SystemExit(f"{suite.toolchain} has no test_suite pin on the {rung} rung in {TOOLCHAINS.name}")
    return entry["test_suite"]


def git(*args: str, cwd: pathlib.Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def fetch(suite: Suite, tag: str, take: list[str], tree: pathlib.Path) -> None:
    """A shallow, partial and sparse checkout of the suite at its tag, or at a commit.

    Only the directories taken and the support headers are checked out, and
    only their blobs are downloaded: llvm-project and GCC are far too large to
    clone whole. A tree already at this tag with this sparse set is reused. A
    development rung pins a full commit hash, since trunk has no tag to name.
    """
    patterns = sorted(set(suite.fetch) | {f"/{t.strip('/')}/" for t in take})
    stamp = tree / ".std-test-suite"
    wanted = json.dumps({"tag": tag, "patterns": patterns})
    if stamp.is_file() and stamp.read_text() == wanted:
        return
    tree.mkdir(parents=True, exist_ok=True)
    if not (tree / ".git").is_dir():
        git("init", "--quiet", cwd=tree)
        git("remote", "add", "origin", suite.repository, cwd=tree)
    git("sparse-checkout", "set", "--no-cone", *patterns, cwd=tree)
    if re.fullmatch(r"[0-9a-f]{40}", tag):
        git("fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", tag, cwd=tree)
    else:
        git("fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", "tag", tag, cwd=tree)
    git("checkout", "--quiet", "--force", tag, cwd=tree)
    stamp.write_text(wanted)


# ---------------------------------------------------------------------------
# The consumer's mapping
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Mapping:
    """The consumer's half, merged over every --mapping file.

    take        directories to take, each with an optional select: a regular
                expression a file's contents must match to be taken at all
    support     support headers to rewrite as a test is, for a suite that
                calls what the library mirrors from a helper rather than from
                the test itself; the copy shadows the original on the include
                path, and a test including it counts as one a rewrite reaches
    include     headers prepended to every rewritten test
    rewrite     regular expressions and their replacements, applied in order
    expect      files expected to fail at compile or run, each with its hash
                and the paragraph the library departs from
    assertion   per-line exceptions: a line, matched after stripping, in a
                file of a given hash, either replaced or dropped, each naming
                the departure it stands for
    """

    take: list[dict] = dataclasses.field(default_factory=list)
    support: list[dict] = dataclasses.field(default_factory=list)
    include: list[str] = dataclasses.field(default_factory=list)
    rewrite: list[dict] = dataclasses.field(default_factory=list)
    expect: list[dict] = dataclasses.field(default_factory=list)
    assertion: list[dict] = dataclasses.field(default_factory=list)


# Each list a mapping may hold, the keys an entry of it must and may carry, and
# whether its entries are tables or strings. A key outside these is an error,
# so a misspelt "fail" or "paragrah" fails loudly rather than leaving an
# expected failure or an exception silently unread.
MAPPING_KEYS: dict[str, tuple[set[str], set[str]] | None] = {
    "take": ({"path"}, {"select"}),
    "support": ({"path"}, set()),
    "include": None,
    "rewrite": ({"to"}, {"from", "regex"}),
    "expect": ({"file", "sha256", "fails", "paragraph"}, set()),
    "assertion": ({"file", "sha256", "line", "departure"}, {"to", "drop"}),
}


def check_scope(scope: dict, where: str) -> None:
    """Reject a key, an entry shape or an entry key a mapping table cannot carry."""
    unknown = set(scope) - set(MAPPING_KEYS)
    if unknown:
        raise SystemExit(f"{where}: unknown keys {sorted(unknown)}; expected {sorted(MAPPING_KEYS)}")
    for key, value in scope.items():
        if not isinstance(value, list):
            raise SystemExit(f"{where}: {key} must be an array")
        shape = MAPPING_KEYS[key]
        for entry in value:
            if shape is None:
                if not isinstance(entry, str):
                    raise SystemExit(f"{where}: every {key} entry must be a string")
                continue
            if not isinstance(entry, dict):
                raise SystemExit(f"{where}: every {key} entry must be a table")
            required, optional = shape
            missing = required - set(entry)
            extra = set(entry) - required - optional
            if missing or extra:
                raise SystemExit(f"{where}: a {key} entry {entry} lacks {sorted(missing)} or carries unknown {sorted(extra)}")


def load_mapping(paths: Iterable[pathlib.Path], suite: str) -> Mapping:
    """Top-level tables apply to every suite; a table named after the suite to that suite alone."""
    mapping = Mapping()
    for path in paths:
        doc = tomllib.loads(path.read_text())
        common = {k: v for k, v in doc.items() if k not in SUITES}
        check_scope(common, str(path))
        for name in SUITES:
            if name in doc:
                if not isinstance(doc[name], dict):
                    raise SystemExit(f"{path}: [{name}] must be a table")
                check_scope(doc[name], f"{path} [{name}]")
        for scope in (common, doc.get(suite, {})):
            for key in MAPPING_KEYS:
                getattr(mapping, key).extend(scope.get(key, []))
    for entry in mapping.rewrite:
        if ("from" in entry) == ("regex" in entry):
            raise SystemExit(f"rewrite {entry}: give exactly one of from and regex")
    for entry in mapping.expect:
        if entry["fails"] not in ("compile", "run"):
            raise SystemExit(f"expected failure {entry['file']}: fails must be compile or run")
        if not entry["paragraph"]:
            raise SystemExit(f"expected failure {entry['file']}: name the paragraph the library departs from")
    for entry in mapping.assertion:
        if not entry["departure"]:
            raise SystemExit(f"exception in {entry['file']}: name the departure it stands for")
        if ("to" in entry) == bool(entry.get("drop")):
            raise SystemExit(f"exception in {entry['file']}: give exactly one of to and drop = true")
    return mapping


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def rewrite(text: str, path: str, digest: str, mapping: Mapping, errors: list[str]) -> tuple[str, list[str], bool]:
    """The build-tree copy of one test, its exceptions applied, and whether any spelling rewrite changed it.

    Exceptions apply to the original lines, then the spelling rewrites to the
    whole text, replacements included.
    """
    applied: list[str] = []
    lines = text.split("\n")
    for entry in mapping.assertion:
        if entry["file"] != path:
            continue
        if entry.get("sha256") != digest:
            errors.append(f"{path}: an exception was written against sha256 {entry.get('sha256')}, the file is now {digest}")
            continue
        wanted = entry["line"].strip()
        hits = [i for i, line in enumerate(lines) if line.strip() == wanted]
        if not hits:
            errors.append(f"{path}: no line reads {wanted!r}")
            continue
        for i in hits:
            indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
            lines[i] = indent + (entry["to"] if "to" in entry else "") + f" // std-test-suite: {entry['departure']}"
        applied.append(f"{wanted} ({entry['departure']})")
    text = "\n".join(lines)
    before = text
    for entry in mapping.rewrite:
        if "regex" in entry:
            text = re.sub(entry["regex"], entry["to"], text)
        else:
            text = text.replace(entry["from"], entry["to"])
    prelude = "".join(f"#include <{header}>\n" for header in mapping.include)
    # The prelude keeps the upstream line numbers, so a diagnostic points at the line the suite wrote.
    return (prelude + "#line 1\n" + text) if prelude else text, applied, text != before


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def collect(suite_name: str, suite: Suite, tree: pathlib.Path, mapping: Mapping, config: Config, errors: list[str]) -> list[Test]:
    tests: list[Test] = []
    expected = {e["file"]: e for e in mapping.expect}
    seen: set[str] = set()
    for entry in mapping.take:
        base = tree / entry["path"].strip("/")
        if not base.is_dir():
            errors.append(f"take {entry['path']}: not a directory at this tag")
            continue
        select = re.compile(entry["select"]) if "select" in entry else None
        for file in sorted(p for p in base.rglob("*") if p.is_file()):
            path = file.relative_to(tree).as_posix()
            if path in seen:
                continue
            seen.add(path)
            data = file.read_bytes()
            text = data.decode("utf-8", errors="replace")
            digest = sha256(data)
            if select and not select.search(text):
                tests.append(Test(path, digest, "skip", f"does not match {entry['select']!r}"))
                continue
            if suite_name == "msvc-stl":
                kind, reason, options, xfail = suite.classify(path, text, config, tree)
            else:
                kind, reason, options, xfail = suite.classify(path, text, config)
            test = Test(path, digest, kind, reason, options)
            if xfail and kind != "skip":
                test.expect, test.paragraph = ("run" if kind == "run" else "compile"), f"the suite's own {xfail}"
            if path in expected:
                e = expected[path]
                if e.get("sha256") != digest:
                    errors.append(f"{path}: its expected failure was written against sha256 {e.get('sha256')}, the file is now {digest}")
                elif kind == "skip":
                    errors.append(f"{path}: expected to fail, but the suite's markers skip it ({reason})")
                elif e["fails"] == "run" and kind != "run":
                    errors.append(f"{path}: expected to fail at run, but it is a {kind} test")
                else:
                    test.expect, test.paragraph = e["fails"], e["paragraph"]
            tests.append(test)
    for path in sorted(set(expected) - seen):
        errors.append(f"{path}: expected to fail, but no taken directory holds it at this tag")
    supported = {s["path"].strip("/") for s in mapping.support}
    for path in sorted({a["file"] for a in mapping.assertion} - seen - supported):
        errors.append(f"{path}: has exceptions, but no taken directory holds it at this tag")
    return tests


def rewrite_support(suite: Suite, tree: pathlib.Path, out: pathlib.Path, mapping: Mapping, errors: list[str]) -> list[dict]:
    """The build-tree copy of each support header the mapping names, under the name a test includes it by.

    A header that does not exist at this tag, lies outside the suite's support
    headers, or that no rewrite changes, is an error: each is a mapping entry
    that no longer does what it was written for.
    """
    records = []
    for entry in mapping.support:
        path = entry["path"].strip("/")
        root = next((s for s in suite.support if path.startswith(s + "/")), None)
        if root is None:
            errors.append(f"support {path}: not under the suite's support headers, {', '.join(suite.support)}")
            continue
        file = tree / path
        if not file.is_file():
            errors.append(f"support {path}: not a file at this tag")
            continue
        data = file.read_bytes()
        digest = sha256(data)
        rewritten, applied, touched = rewrite(data.decode("utf-8", errors="replace"), path, digest, mapping, errors)
        if not touched:
            errors.append(f"support {path}: no rewrite changes it")
            continue
        name = path.removeprefix(root + "/")
        copy = out / "support" / name
        copy.parent.mkdir(parents=True, exist_ok=True)
        if not copy.is_file() or copy.read_text() != rewritten:
            copy.write_text(rewritten)
        records.append({"path": path, "sha256": digest, "name": name, "exceptions": applied})
    return records


def includes_any(text: str, names: Iterable[str]) -> bool:
    """Whether the text includes one of these headers by name, in quotes or angle brackets."""
    return any(re.search(r"#\s*include\s*[\"<]" + re.escape(name) + r"[\">]", text) for name in names)


def target_name(suite_name: str, suite: Suite, path: str) -> str:
    stem = path.removeprefix(suite.root)
    for suffix in (".compile.pass.cpp", ".link.pass.cpp", ".pass.cpp", ".cpp", ".cc"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return re.sub(r"[^A-Za-z0-9_.+-]", ".", f"{suite_name}.{stem}")


def cmake_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace(";", "\\;") + '"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", required=True, choices=sorted(SUITES))
    parser.add_argument("--mapping", required=True, action="append", type=pathlib.Path)
    parser.add_argument("--tree", required=True, type=pathlib.Path, help="where the suite is checked out")
    parser.add_argument("--binary-dir", required=True, type=pathlib.Path, help="where the rewritten tests and tests.cmake go")
    parser.add_argument("--rung", default="stable")
    parser.add_argument("--tag", default="", help="a tag in place of the rung's pin, for a bump")
    parser.add_argument("--compiler", required=True, choices=["gcc", "clang", "apple-clang", "msvc"])
    parser.add_argument("--version", required=True)
    parser.add_argument("--stdlib", required=True, choices=["libstdc++", "libc++", "msvc"])
    parser.add_argument("--std", required=True, type=int)
    parser.add_argument("--target", required=True)
    parser.add_argument("--no-exceptions", action="store_true")
    parser.add_argument("--no-rtti", action="store_true")
    parser.add_argument("--offline", action="store_true", help="use the tree as it is, without fetching")
    args = parser.parse_args(argv)

    suite = SUITES[args.suite]
    config = Config(args.compiler, args.version, args.stdlib, args.std, args.target, not args.no_exceptions, not args.no_rtti)
    mapping = load_mapping(args.mapping, args.suite)
    if not mapping.take:
        raise SystemExit(f"the mapping takes no directory from {args.suite}")
    tag = args.tag or pinned_tag(suite, args.rung)
    if not args.offline:
        fetch(suite, tag, [t["path"] for t in mapping.take], args.tree)

    errors: list[str] = []
    tests = collect(args.suite, suite, args.tree, mapping, config, errors)

    out = args.binary_dir
    support = rewrite_support(suite, args.tree, out, mapping, errors)
    sources = out / "tests"
    calls = []
    for test in tests:
        if test.kind == "skip":
            continue
        text = (args.tree / test.path).read_bytes().decode("utf-8", errors="replace")
        rewritten, test.exceptions, touched = rewrite(text, test.path, test.sha256, mapping, errors)
        # A test no rewrite reaches still names only the standard library's types, and would pass on its account.
        if not touched and not includes_any(text, (s["name"] for s in support)):
            if test.expect and not test.paragraph.startswith("the suite's own"):
                errors.append(f"{test.path}: expected to fail, but no rewrite reaches it")
            test.kind, test.reason, test.expect, test.paragraph = "skip", "no rewrite reaches it, so it tests the standard library", "", ""
            continue
        source = sources / test.path
        source.parent.mkdir(parents=True, exist_ok=True)
        if not source.is_file() or source.read_text() != rewritten:
            source.write_text(rewritten)
        calls.append(
            f"std_test_suite_add({target_name(args.suite, suite, test.path)}"
            f" SUITE {args.suite} KIND {test.kind} SOURCE {cmake_quote(source.as_posix())}"
            # The copy's relative includes resolve from the directory its original sits in.
            f" INCLUDE_DIRS {cmake_quote((args.tree / test.path).parent.resolve().as_posix())}"
            + (f" EXPECT {test.expect}" if test.expect else "")
            + (" OPTIONS " + " ".join(cmake_quote(o) for o in test.options) if test.options else "")
            + ")"
        )
    if errors:
        for error in errors:
            print(f"std_test_suite: {error}", file=sys.stderr)
        return 1

    # The rewritten support headers come first, so a test's include finds the copy before the original.
    roots = ([out / "support"] if support else []) + [args.tree / s for s in suite.support]
    includes = " ".join(cmake_quote(r.as_posix()) for r in roots)
    body = [
        f"# Generated by std_test_suite.py for {args.suite} at {tag}; do not edit.",
        f"set(STD_TEST_SUITE_INCLUDE_DIRS_{args.suite.replace('-', '_')} {includes})",
        *calls,
        "",
    ]
    # A suite whose every test is skipped writes no copy, so nothing above has made the directory.
    out.mkdir(parents=True, exist_ok=True)
    cmake_file = out / "tests.cmake"
    if not cmake_file.is_file() or cmake_file.read_text() != "\n".join(body):
        cmake_file.write_text("\n".join(body))
    manifest = {
        "suite": args.suite,
        "repository": suite.repository,
        "tag": tag,
        "support": [{k: v for k, v in s.items() if v} for s in support],
        "tests": [{k: v for k, v in dataclasses.asdict(t).items() if v or k in ("path", "sha256", "kind")} for t in tests],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    counts: dict[str, int] = {}
    for test in tests:
        key = f"expected {test.expect} failure" if test.expect else test.kind
        counts[key] = counts.get(key, 0) + 1
    rewritten_headers = f", {len(support)} rewritten support header{'s' if len(support) != 1 else ''}" if support else ""
    print(f"std_test_suite: {args.suite} at {tag}: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())) + rewritten_headers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
