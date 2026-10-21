#!/usr/bin/env python3
#          Copyright Rein Halbersma 2026.
# Distributed under the Boost Software License, Version 1.0.
#    (See accompanying file LICENSE_1_0.txt or copy at
#          http://www.boost.org/LICENSE_1_0.txt)

"""Fetch a GCC release from ppa:ubuntu-toolchain-r/test without the PPA's mirror.

apt reaches the PPA through ppa.launchpadcontent.net, which can answer 503 for
hours while Launchpad itself is up: on 2026-10-21 it refused nearly every
request from 15:19 UTC into the night, and every leg installing a GCC release
failed before building anything. The same packages stay downloadable from the
Launchpad librarian, through the archive's +files URLs, and the Launchpad API
names each one with its sha256.

This script downloads the packages the PPA would supply to an install of
g++-<version> on this runner, checks each against the sha256 the API reports,
and leaves them in one directory for apt-get install to take as local files.
The set is what apt itself takes from the PPA:

- g++-<version>, and g++-<version>-multilib when asked for, the requested ones;
- every package the PPA publishes that one of the set depends on, the first
  alternative of each dependency as apt resolves it, recursively;
- every package already installed here that the PPA builds from a source the
  two steps above reached, as gcc-16's runtime libraries are for g++-15: the
  PPA route upgrades those in the same transaction, and leaves the runner's
  own gcc-13 and gcc-14 alone.

Dependencies the PPA does not publish are left to the Ubuntu archive, which apt
reaches as usual. Only the standard library is used, so the script runs on the
runner's own Python before anything is installed.
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable

API = "https://api.launchpad.net/devel"
ARCHIVE = f"{API}/~ubuntu-toolchain-r/+archive/ubuntu/test"

# A publication names a package's version, source and file; a package adds the
# dependencies read from that file once it is downloaded.
Publication = dict[str, str]
Package = dict[str, object]


def first_alternatives(field: str) -> list[str]:
    """Name the package apt tries first in each clause of a Depends field."""
    names = []
    for clause in field.split(","):
        clause = clause.strip()
        if not clause:
            continue
        first = clause.split("|")[0].strip()
        name = re.split(r"[\s(\[<]", first, maxsplit=1)[0]
        # An architecture qualifier names the same package for dpkg.
        names.append(name.split(":")[0])
    return names


def closure(
    wanted: Iterable[str],
    installed: Iterable[str],
    publication: Callable[[str], Publication | None],
    download: Callable[[Publication], Package],
) -> dict[str, Package]:
    """Collect what the PPA supplies to an install of the wanted packages."""
    taken: dict[str, Package] = {}

    def take(names: Iterable[str]) -> None:
        queue = list(names)
        while queue:
            name = queue.pop(0)
            if name in taken:
                continue
            published = publication(name)
            if published is None:
                continue
            taken[name] = download(published)
            queue.extend(taken[name]["depends"])

    for name in wanted:
        if publication(name) is None:
            raise LookupError(f"{name} is not published in ppa:ubuntu-toolchain-r/test for this series")
    take(wanted)
    sources = {package["source"] for package in taken.values()}
    take(
        name
        for name in sorted(installed)
        if name not in taken and (published := publication(name)) is not None and published["source"] in sources
    )
    return taken


def get(url: str, attempts: int = 5) -> bytes:
    """Fetch a URL, retrying the transient failures the librarian also has."""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            # A 404 is an answer, not a brownout.
            if isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429:
                raise
            if attempt == attempts:
                raise
            print(f"retrying {url} after {error}", file=sys.stderr)
            time.sleep(attempt * 10)
    raise AssertionError("unreachable")


def api(url: str, **params: str) -> dict:
    query = urllib.parse.urlencode(params)
    return json.loads(get(f"{url}?{query}" if query else url))


class Librarian:
    """Look packages up through the API, and download them into one directory."""

    def __init__(self, series: str, arch: str, dest: pathlib.Path) -> None:
        self.arch_series = f"{API}/ubuntu/{series}/{arch}"
        self.dest = dest
        self.cache: dict[str, Publication | None] = {}

    def publication(self, name: str) -> Publication | None:
        if name not in self.cache:
            found = api(
                ARCHIVE,
                **{
                    "ws.op": "getPublishedBinaries",
                    "binary_name": name,
                    "exact_match": "true",
                    "status": "Published",
                    "distro_arch_series": self.arch_series,
                    "order_by_date": "true",
                },
            )["entries"]
            # Newest first, as order_by_date returns them: apt's candidate.
            self.cache[name] = None if not found else {
                "name": name,
                "version": found[0]["binary_package_version"],
                "source": found[0]["source_package_name"],
                "self_link": found[0]["self_link"],
            }
        return self.cache[name]

    def download(self, published: Publication) -> Package:
        files = json.loads(get(f"{published['self_link']}?ws.op=binaryFileUrls&include_meta=true"))
        debs = [f for f in files if f["url"].endswith(".deb")]
        if len(debs) != 1:
            raise LookupError(f"{published['name']}: expected one .deb, the API lists {len(debs)}")
        meta = debs[0]
        path = self.dest / urllib.parse.unquote(meta["url"].rsplit("/", 1)[1])
        data = get(meta["url"])
        digest = hashlib.sha256(data).hexdigest()
        if digest != meta["sha256"]:
            raise ValueError(f"{path.name}: sha256 {digest}, the API says {meta['sha256']}")
        path.write_bytes(data)
        print(f"{digest}  {path.name}")
        fields = subprocess.run(
            ["dpkg-deb", "--field", str(path), "Pre-Depends", "Depends"],
            capture_output=True, text=True, check=True,
        ).stdout
        depends = []
        for line in fields.splitlines():
            _, _, value = line.partition(":")
            depends += first_alternatives(value)
        return {**published, "depends": depends}


def installed_from_gcc() -> list[str]:
    """Name the packages installed here whose source is a GCC release."""
    listing = subprocess.run(
        ["dpkg-query", "--show", "--showformat", "${db:Status-Status} ${Package} ${source:Package}\n"],
        capture_output=True, text=True, check=True,
    ).stdout
    names = []
    for line in listing.splitlines():
        status, name, source = (line.split() + ["", "", ""])[:3]
        if status == "installed" and re.fullmatch(r"gcc-\d+", source):
            names.append(name)
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="GCC major version, e.g. 15")
    parser.add_argument("--multilib", action="store_true", help="also take g++-<version>-multilib")
    parser.add_argument("--dest", required=True, type=pathlib.Path, help="directory to download into")
    args = parser.parse_args()

    release = dict(
        line.split("=", 1) for line in pathlib.Path("/etc/os-release").read_text().splitlines() if "=" in line
    )
    series = release["VERSION_CODENAME"].strip('"')
    arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True, text=True, check=True).stdout.strip()

    args.dest.mkdir(parents=True, exist_ok=True)
    wanted = [f"g++-{args.version}"] + ([f"g++-{args.version}-multilib"] if args.multilib else [])
    librarian = Librarian(series, arch, args.dest)
    taken = closure(wanted, installed_from_gcc(), librarian.publication, librarian.download)
    print(f"{len(taken)} package(s) from the librarian for {series}/{arch}:")
    for package in sorted(taken.values(), key=lambda p: p["name"]):
        print(f"  {package['name']} {package['version']} ({package['source']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
