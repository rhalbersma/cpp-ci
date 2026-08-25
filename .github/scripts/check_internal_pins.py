#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Check that this repository's own pins point at commits that can serve them.

A workflow added in the same commit as the action it names, or as the ladder
entry it resolves, pins the commit *before* itself -- which does not contain
either. Nothing local catches that: the file is valid YAML, actionlint is
happy, and the failure appears only when a runner tries to fetch the action or
resolve the family. Five legs failed that way at once, so it is checked here.
"""

import json
import pathlib
import subprocess
import sys

import yaml

REPO = "rhalbersma/cpp-ci/"
TABLE = ".github/actions/toolchain/toolchains.json"


def at(sha: str, path: str) -> bytes | None:
    out = subprocess.run(["git", "show", f"{sha}:{path}"], capture_output=True)
    return out.stdout if out.returncode == 0 else None


def main() -> int:
    failures = []
    seen_pins: dict[str, set[str]] = {}
    for wf in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
        doc = yaml.safe_load(wf.read_text())
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                uses = step.get("uses", "")
                if not uses.startswith(REPO):
                    continue
                path, _, sha = uses[len(REPO):].partition("@")

                manifest = at(sha, f"{path}/action.yml")
                if manifest is None:
                    failures.append(f"{wf.name}: {path} does not exist at {sha[:7]}")
                    continue

                # An input added in the same commit as its first caller is the
                # same mistake one level down: the action resolves, and then
                # ignores what it was handed.
                seen_pins.setdefault(sha, set()).add(wf.name)

                declared = set(yaml.safe_load(manifest).get("inputs") or {})
                for given in (step.get("with") or {}):
                    if given not in declared:
                        failures.append(
                            f"{wf.name}: {path} at {sha[:7]} has no input '{given}'"
                        )

                # The ladder is resolved from data, so the pin has to carry
                # the family as well as the action that reads it.
                # A family chosen per matrix leg is not knowable here; the
                # workflow's own "Require the rung to be filled" step catches
                # an unfilled one at run time.
                family = (step.get("with") or {}).get("toolchain")
                if family and "${{" in family:
                    family = None
                if family and path.endswith("/toolchain"):
                    table = at(sha, TABLE)
                    if table is None or family not in json.loads(table):
                        failures.append(
                            f"{wf.name}: toolchains.json at {sha[:7]} has no '{family}'"
                        )

    # Every internal pin must name the same commit. Each one on its own can
    # be perfectly valid -- the action exists there, its inputs are declared --
    # while the set of them is still wrong, because a workflow pinned at an
    # older commit runs an older copy of the action. That is not theoretical:
    # a retry widened in install-gcc reached nothing for an hour, since all
    # seven workflows calling it named commits from before the change, and
    # three legs then failed on the very brownout it was written for.
    #
    # Pinning them together also keeps this repository honest about what it
    # sells: one place to change a thing, not fourteen commits' worth of drift
    # inside the repository that exists to end drift elsewhere.
    if len(seen_pins) > 1:
        listed = ", ".join(
            f"{sha[:7]} ({', '.join(sorted(names))})"
            for sha, names in sorted(seen_pins.items())
        )
        failures.append(
            f"internal pins name {len(seen_pins)} different commits: {listed}"
        )

    for f in failures:
        print(f"::error::{f}")
    print(f"{len(failures)} bad pin(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
