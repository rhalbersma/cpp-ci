#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Report whether a group of repositories agree on the files they share.

A report, never a gate: drift is found and shown, and it is the reader's to
either explain in the file or remove. The exit status is non-zero only for a
malformed invocation; a file that differs, is absent, or cannot be read is a
line in the report, not a failure.

Settings come from the environment:

  REPOSITORIES       whitespace-separated owner/name, each optionally @ref
  FILES              whitespace-separated paths to compare across them
  PINNED_REPOSITORY  owner/name whose pins in .github/workflows to list per
                     repository; empty lists none
  GITHUB_TOKEN       token for the GitHub REST API; empty reads anonymously
  GITHUB_API_URL     the API root, as the runner sets it

For each file the report groups the repositories by content and names the
newest variant, by the date of the last commit touching the file: where two
repositories differ on a convention, the most recent change is the usual
candidate for it. Every other variant is shown as a diff against that one.

The report goes to stdout and, on a runner, to GITHUB_STEP_SUMMARY; the files
that disagree go to GITHUB_OUTPUT as `drifted`, space-separated.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

DIFF_LINE_LIMIT = 200


@dataclasses.dataclass(frozen=True)
class Repository:
    name: str
    ref: str | None

    @property
    def label(self) -> str:
        return f"{self.name}@{self.ref}" if self.ref else self.name


@dataclasses.dataclass(frozen=True)
class Fetched:
    """One file in one repository: its bytes, None when absent, or an error."""

    content: bytes | None
    date: str | None = None
    error: str | None = None


@dataclasses.dataclass
class Variant:
    content: bytes
    repositories: list[Repository]
    date: str | None


@dataclasses.dataclass
class FileReport:
    path: str
    variants: list[Variant]  # newest first
    absent: list[Repository]
    unreadable: list[tuple[Repository, str]]

    @property
    def drifted(self) -> bool:
        return len(self.variants) + (1 if self.absent else 0) > 1


_REPOSITORY = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:@(\S+))?$")


def parse_repositories(text: str) -> list[Repository]:
    repositories = []
    for word in text.split():
        match = _REPOSITORY.match(word)
        if not match:
            raise ValueError(f"not owner/name or owner/name@ref: {word!r}")
        repositories.append(Repository(match[1], match[2]))
    if len(repositories) < 2:
        raise ValueError("comparing needs at least two repositories")
    labels = [r.label for r in repositories]
    duplicates = sorted({label for label in labels if labels.count(label) > 1})
    if duplicates:
        raise ValueError(f"named twice: {', '.join(duplicates)}")
    return repositories


def parse_files(text: str) -> list[str]:
    files = text.split()
    if not files:
        raise ValueError("no files to compare")
    return list(dict.fromkeys(files))


def classify(path: str, fetched: dict[Repository, Fetched]) -> FileReport:
    variants: dict[bytes, Variant] = {}
    absent = []
    unreadable = []
    for repository, item in fetched.items():
        if item.error is not None:
            unreadable.append((repository, item.error))
        elif item.content is None:
            absent.append(repository)
        elif item.content in variants:
            variant = variants[item.content]
            variant.repositories.append(repository)
            variant.date = max(filter(None, [variant.date, item.date]), default=None)
        else:
            variants[item.content] = Variant(item.content, [repository], item.date)
    # ISO 8601 dates sort as text; an undated variant sorts last, the rest by
    # the most repositories sharing it, so the order never depends on dicts.
    ordered = sorted(
        variants.values(),
        key=lambda v: (v.date or "", len(v.repositories)),
        reverse=True,
    )
    return FileReport(path, ordered, absent, unreadable)


_PIN = r"uses:\s*{prefix}/[^@\s]+@([0-9a-f]{{40}})(?:[ \t]*#[ \t]*([^\n]*))?"


def find_pins(workflow: str, pinned: str) -> list[tuple[str, str]]:
    """The (sha, comment) of each `uses:` line naming the pinned repository."""
    pattern = re.compile(_PIN.format(prefix=re.escape(pinned)))
    return [(m[1], (m[2] or "").strip()) for m in pattern.finditer(workflow)]


def summarize_pins(pins: Iterable[tuple[str, str]]) -> str:
    distinct = sorted(set(pins))
    if not distinct:
        return "none"
    return ", ".join(f"`{sha[:7]}`" + (f" {comment}" if comment else "") for sha, comment in distinct)


def _text(content: bytes) -> list[str]:
    return content.decode("utf-8", errors="replace").splitlines(keepends=True)


def render(
    reports: list[FileReport],
    repositories: list[Repository],
    pins: dict[Repository, str] | None,
) -> str:
    lines = ["## Config drift", ""]
    lines.append(f"{len(repositories)} repositories: " + ", ".join(f"`{r.label}`" for r in repositories) + ".")
    lines.append("")
    lines += ["| File | Agreement |", "| :--- | :-------- |"]
    for report in reports:
        lines.append(f"| `{report.path}` | {_agreement(report)} |")
    lines.append("")

    if pins is not None:
        lines += ["| Repository | Pins |", "| :--------- | :--- |"]
        for repository in repositories:
            lines.append(f"| `{repository.label}` | {pins[repository]} |")
        lines.append("")

    for report in reports:
        if len(report.variants) < 2:
            continue
        newest = report.variants[0]
        base = newest.repositories[0].label
        for variant in report.variants[1:]:
            other = variant.repositories[0].label
            diff = list(difflib.unified_diff(
                _text(newest.content), _text(variant.content),
                fromfile=f"{base}/{report.path}", tofile=f"{other}/{report.path}",
            ))
            shown = diff[:DIFF_LINE_LIMIT]
            if len(diff) > DIFF_LINE_LIMIT:
                shown.append(f"... {len(diff) - DIFF_LINE_LIMIT} more lines\n")
            lines.append(f"<details><summary><code>{report.path}</code>: {_names(variant.repositories, html=True)} against {_names(newest.repositories, html=True)}</summary>")
            lines += ["", "```diff", "".join(shown).rstrip("\n"), "```", "", "</details>", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def _names(repositories: list[Repository], html: bool = False) -> str:
    # Markdown is not read inside a <summary>, so a backtick there shows as one.
    open_, close = ("<code>", "</code>") if html else ("`", "`")
    return ", ".join(f"{open_}{r.label}{close}" for r in repositories)


def _agreement(report: FileReport) -> str:
    parts = []
    if not report.drifted:
        if report.variants:
            parts.append(f"all {len(report.variants[0].repositories)} agree")
        elif report.absent:
            parts.append("absent from all")
    else:
        for index, variant in enumerate(report.variants):
            tag = "newest" if index == 0 and variant.date else "variant"
            when = f", {variant.date[:10]}" if variant.date else ""
            parts.append(f"{tag}{when}: {_names(variant.repositories)}")
        if report.absent:
            parts.append(f"absent: {_names(report.absent)}")
    if report.unreadable:
        parts.append("unreadable: " + ", ".join(f"`{r.label}` ({error})" for r, error in report.unreadable))
    return "; ".join(parts) if parts else "nothing read"


class GitHub:
    """The few REST calls the report needs, read-only."""

    def __init__(self, api: str, token: str) -> None:
        self.api = api.rstrip("/")
        self.token = token

    def _get(self, path: str, query: dict[str, str], accept: str) -> bytes | None:
        url = f"{self.api}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "cpp-ci-config-drift",
        })
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def _ref(self, repository: Repository) -> dict[str, str]:
        return {"ref": repository.ref} if repository.ref else {}

    def file(self, repository: Repository, path: str) -> Fetched:
        quoted = urllib.parse.quote(path)
        try:
            content = self._get(f"/repos/{repository.name}/contents/{quoted}", self._ref(repository), "application/vnd.github.raw+json")
            if content is None:
                return Fetched(None)
            query = {"path": path, "per_page": "1"}
            if repository.ref:
                query["sha"] = repository.ref
            commits = json.loads(self._get(f"/repos/{repository.name}/commits", query, "application/vnd.github+json") or b"[]")
            date = commits[0]["commit"]["committer"]["date"] if commits else None
            return Fetched(content, date)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            return Fetched(None, error=str(error))

    def workflows(self, repository: Repository) -> list[str] | str:
        """Each workflow's text, or the error that kept them from being read."""
        try:
            listing = self._get(f"/repos/{repository.name}/contents/.github/workflows", self._ref(repository), "application/vnd.github+json")
            if listing is None:
                return []
            texts = []
            for entry in json.loads(listing):
                if entry.get("type") == "file" and entry["name"].endswith((".yml", ".yaml")):
                    text = self._get(f"/repos/{repository.name}/contents/{urllib.parse.quote(entry['path'])}", self._ref(repository), "application/vnd.github.raw+json")
                    if text is not None:
                        texts.append(text.decode("utf-8", errors="replace"))
            return texts
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            return str(error)


def report(
    repositories: list[Repository],
    files: list[str],
    pinned: str,
    fetch_file: Callable[[Repository, str], Fetched],
    fetch_workflows: Callable[[Repository], list[str] | str],
) -> tuple[str, list[FileReport]]:
    reports = [classify(path, {r: fetch_file(r, path) for r in repositories}) for path in files]
    pins = None
    if pinned:
        pins = {}
        for repository in repositories:
            workflows = fetch_workflows(repository)
            if isinstance(workflows, str):
                pins[repository] = f"unreadable ({workflows})"
            else:
                pins[repository] = summarize_pins(pin for text in workflows for pin in find_pins(text, pinned))
    return render(reports, repositories, pins), reports


def main() -> int:
    try:
        repositories = parse_repositories(os.environ.get("REPOSITORIES", ""))
        files = parse_files(os.environ.get("FILES", ""))
    except ValueError as error:
        print(f"::error::{error}")
        return 2
    github = GitHub(os.environ.get("GITHUB_API_URL") or "https://api.github.com", os.environ.get("GITHUB_TOKEN", ""))
    text, reports = report(repositories, files, os.environ.get("PINNED_REPOSITORY", ""), github.file, github.workflows)

    print(text)
    for item in reports:
        for repository, error in item.unreadable:
            print(f"::warning::{repository.label}: {item.path} unreadable: {error}")
    drifted = " ".join(item.path for item in reports if item.drifted)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(text)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"drifted={drifted}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
