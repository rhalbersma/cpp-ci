#!/usr/bin/env python3

#          Copyright Rein Halbersma 2014-2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Check that a library's headers are layered: implementation pushed down, interface pulled up.

    layering.py <config.toml>

The configuration, a path relative to the working directory, names the folder whose subfolders are
classified, and the kind of each:

    root = "include/xstd/bits"        # its headers are included as <xstd/bits/...>
    shared = "detail"                 # the bottom: every definition more than one folder needs
    layers = ["bit", "bit_concepts"]  # public fronts over the shared folder and the layers below
    families = ["bit_set"]            # the top, one folder each; optional
    extensions = ["ext"]              # build on whichever family they extend; optional

Five rules hold, read from the #include lines alone:

- the shared folder includes no public header of the library, so nothing below a front depends on one;
- no folder reaches back to a folder that reaches it, so the folders stack without a cycle;
- no family includes another, so deleting one leaves every other building;
- a public header is included by umbrellas and extensions alone, so deleting one breaks only those;
- a folder's own detail/ is included from that folder alone, so deleting a folder breaks no other.

An umbrella is a header directly under the root, named after the folder it gathers, as bit_set.hpp
gathers bit_set/. Umbrellas gather and extensions extend, so neither is bound by the last three rules. A folder the configuration does not classify is an error: a new folder
says which kind it is before it lands.
"""

import re
import sys
import tomllib
from pathlib import Path


def load(path):
    """The configuration, with the optional kinds defaulted, and the include prefix the root implies."""
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    root = Path(config["root"])
    parts = root.parts
    if "include" not in parts or parts.index("include") == len(parts) - 1:
        raise SystemExit(f"{path}: root {config['root']!r} names no folder under an include/ directory")
    prefix = "/".join(parts[parts.index("include") + 1:])
    return root, prefix, config["shared"], set(config["layers"]), set(config.get("families", [])), set(config.get("extensions", []))


def folder(relative):
    """The folder a header under the root belongs to: its first component, or its own name for an umbrella."""
    parts = relative.split("/")
    return parts[0] if len(parts) > 1 else parts[0].removesuffix(".hpp")


def is_umbrella(relative):
    """An umbrella: a header directly under the root, which gathers its folder."""
    return "/" not in relative


def is_detail(relative, shared):
    """A header under a detail folder, the shared one or a folder's own."""
    return relative.startswith(f"{shared}/") or "/detail/" in relative


def cycles(edges):
    """Tarjan's strongly connected components over the folder graph: any with more than one folder is a cycle."""
    index, low, stack, on_stack, found = {}, {}, [], set(), []

    def visit(node):
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for successor in sorted(edges.get(node, ())):
            if successor not in index:
                visit(successor)
                low[node] = min(low[node], low[successor])
            elif successor in on_stack:
                low[node] = min(low[node], index[successor])
        if low[node] == index[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                found.append(sorted(component))

    for node in sorted(edges):
        if node not in index:
            visit(node)
    return found


def check(config_path):
    """The layering errors of the tree the configuration describes, and a one-line summary."""
    root, prefix, shared, layers, families, extensions = load(config_path)
    if not root.is_dir():
        raise SystemExit(f"{config_path}: root {root.as_posix()} is not a directory")
    include = re.compile(r"^#include <" + re.escape(prefix) + r"/([^>]+)>", re.MULTILINE)
    headers = {path.relative_to(root).as_posix(): include.findall(path.read_text(encoding="utf-8")) for path in sorted(root.rglob("*.hpp"))}

    errors = []
    known = {shared} | layers | families | extensions
    for name in sorted({folder(h) for h in headers} - known):
        errors.append(f"{root.as_posix()}/{name}: an unclassified folder; list it as a layer or a family in {config_path.as_posix()}")

    edges = {}
    for header, includes in headers.items():
        source = folder(header)
        for included in includes:
            target = folder(included)
            where = f"{root.as_posix()}/{header}"
            if source == shared and target != shared:
                errors.append(f"{where}: {shared}/ includes the public <{prefix}/{included}>")
            if source in families and target in families and source != target:
                errors.append(f"{where}: the {source} family includes the {target} family's <{prefix}/{included}>")
            if source != target and source not in extensions:
                edges.setdefault(source, set()).add(target)
            exempt = is_umbrella(header) or source in extensions
            if not exempt and not is_detail(included, shared) and not is_umbrella(included) and target not in extensions:
                errors.append(f"{where}: includes the public <{prefix}/{included}>, which only umbrellas and extensions include")
            if source not in extensions and is_detail(included, shared) and target not in (shared, source):
                errors.append(f"{where}: includes the {target} folder's own <{prefix}/{included}>")

    for cycle in cycles(edges):
        errors.append(f"{root.as_posix()}: the folders {', '.join(cycle)} reach one another")

    summary = f"{len(headers)} headers, {len(edges)} folders with outgoing includes, {len(errors)} layering errors"
    return errors, summary


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: layering.py <config.toml>")
    errors, summary = check(Path(sys.argv[1]))
    print(summary)
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
