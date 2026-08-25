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
    for wf in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
        doc = yaml.safe_load(wf.read_text())
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                uses = step.get("uses", "")
                if not uses.startswith(REPO):
                    continue
                path, _, sha = uses[len(REPO):].partition("@")

                if at(sha, f"{path}/action.yml") is None:
                    failures.append(f"{wf.name}: {path} does not exist at {sha[:7]}")
                    continue

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

    for f in failures:
        print(f"::error::{f}")
    print(f"{len(failures)} bad pin(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
