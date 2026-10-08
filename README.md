# fuzzing — Claude Code plugin for AFL++ campaigns

Skills that let Claude prepare, run and triage AFL++ fuzzing campaigns against C/C++ libraries, write
vulnerability reports a maintainer can act on, and carry findings forward to new upstream releases.

| Skill | Does |
|---|---|
| `/fuzzing:fuzz-campaign <project> <version> <campaign> <goal>` | Checks out the project at the release tag, builds it with AFL++ and ASan, writes a harness for the goal, generates seeds, verifies the binary, commits the campaign on `fuzzing/<version>` |
| `/fuzzing:fuzz-run <project>/<version>/<campaign>` | Starts the campaign on 80% of the cores (or on a remote server), keeps a dashboard, reports real findings, triages crashes vs hangs, verifies against plain builds, minimises reproducers, continues fuzzing on the patched branch |
| `/fuzzing:vuln-report <project>/<version>` | One report per root cause (evidence, reproduction without our harness, CVSS, patch, regression test), fixes as commits on `patched/<version>`, `status.json` per vulnerability, independent verification, disclosure package and tag |
| `/fuzzing:fuzz-rebase <project> <new-version>` | Moves campaigns to a new release, replays every saved input on the unpatched release, marks vulnerabilities fixed or still open, rebuilds the patch branch |

## Install

```
/plugin marketplace add <path-or-url-of-this-repository>
/plugin install fuzzing@fuzzing-skill
```

After editing a skill in a local checkout, run `/reload-plugins`.

## Workspace

The skills work in a workspace directory of your choice (`$FUZZ_ROOT`, or the current directory) and never write its name anywhere:

```
<project>/
  .fork/                  clone of the project; upstream push disabled
  <version>/
    src/                  worktree: fuzzing/<version> (campaigns under fuzz/<campaign>/) or patched/<version> (known findings fixed)
    work/                 scratch builds
    <campaign>/           out/ (raw AFL++ output), findings/ (curated), problems.md
    VULN-NN-<slug>/       report.md, fix.patch, status.json, poc/
    repro/ triage/ INDEX.md disclosure_<date>.md
```

Keep the workspace in a **private** repository: it holds working crash inputs and unpublished vulnerability details.
`src/`, `work/` and `out/` are not committed there; campaigns live on the fork's branches. This repository contains
only the generic plugin — never campaign files, findings or reports.

## Requirements

Ubuntu 24.04 or similar with `afl++`, `clang-17` and `cmake` (the skills install what is missing). AFL++ needs
`kernel.core_pattern=core` on the host; the remote mode prepares a server itself.
