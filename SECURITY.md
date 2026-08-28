# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it privately
using [GitHub Security Advisories](https://github.com/rhalbersma/cpp-ci/security/advisories/new)
rather than opening a public issue.

This project is maintained by a single volunteer on a reasonable-effort basis.
As such, please allow at least 90 days to work on a fix before public disclosure.

## Scope

This repository ships no runtime code. It is a set of reusable GitHub Actions
workflows and composite actions that other repositories call, so the security
surface is the workflows themselves: what they run, what they trust, and what
permissions they ask a caller to grant.

Reports about any of the following are in scope:

- A workflow or action that lets a caller's input reach a shell, an environment
  file or a job's permissions in a way the caller did not intend.
- A dependency reference that is not pinned to a commit, or a pin that does not
  match what the version comment beside it claims.
- A grant wider than the job needs, at workflow level where a job level would do.

Because every caller pins a commit of this repository, a fix here reaches them
only when they repin. Advisories will say so, and name the first tag carrying
the fix.
