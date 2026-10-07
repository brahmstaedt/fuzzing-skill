---
name: fuzz-rebase
description: Carry a fuzzing project forward to a new upstream release — new fuzzing/<version> branch with the existing campaigns, replay every saved crash and hang input of every earlier vulnerability against the unpatched new release, mark vulnerabilities "no longer reproduces" or "fixed upstream", and rebuild the temporary patched/<version> branch from the fixes still needed. Use for "new release of <project>", "rebase the fuzzing to <version>", "is VULN-NN fixed upstream", "re-run our crashes on the new version".
---

# Moving fuzzing to a new upstream release

`/fuzzing:fuzz-rebase <project> <new-version>` — paths are relative to the workspace (`$FUZZ_ROOT` or the current directory; layout in
`/fuzzing:fuzz-campaign`). `O` = the previous version folder `<project>/<old-version>` (ask if there is more than one candidate), `N` = `<project>/<new-version>`.
Log every failure or wrong turn to `N/<campaign>/problems.md` as in `/fuzzing:fuzz-campaign`.

The idea: while a version is fuzzed, the temporary branch `patched/<old-version>` carries our fixes so the fuzzer gets past known findings.
We assume upstream eventually fixes them. A new release therefore starts **without** our fixes; each one comes back only if its
vulnerability still reproduces on the new release.

## 1. New source and campaigns
```bash
git -C <project>/.fork fetch upstream --tags
git -C <project>/.fork tag -l '*<new-version>*'                      # find the release tag
git -C <project>/.fork worktree add -b fuzzing/<new-version> ../<new-version>/src <release-tag>
git -C N/src checkout fuzzing/<old-version> -- fuzz                  # all campaigns of the old version, unchanged
```
Commit `fuzz/` on `fuzzing/<new-version>` ("carry campaigns over from <old-version>"). Fix version-specific lines (module lists, flags) only where
the build needs it, as separate commits. Build every campaign (`N/src/fuzz/*/build.sh`) and check each harness on its seeds
(`/fuzzing:fuzz-campaign` §4) before anything else: a replay against a broken build proves nothing.

## 2. Replay every known vulnerability on the unpatched release
For each `O/VULN-NN-*/` (and older versions' vulnerabilities whose `status.json` state is not yet `fixed-upstream`):
- Inputs: its `poc/` files plus the curated findings of the signatures it covers (`O/<campaign>/findings/`, `minimized/` + the originals).
- Replay each input on the new ASan harness **and** on a plain build of the new release (the report's own reproduction: shipped tool or
  `O/repro/`), exactly as the report describes. Record per vulnerability: inputs, reproduced, ASan class @ frame, plain-build symptom.
- Append the result to `replays` in its `status.json` (version, date, asan, plain, inputs, reproduced). Never edit earlier entries.

Then decide, per vulnerability:
- **Still reproduces** (any input, same class at the same site or the same root cause): the fix is still needed (§3). Leave `state` as it is.
- **No input reproduces**: set `state` to `no-longer-reproduces`. This is not yet "fixed": the input may now be rejected earlier by an
  unrelated change, or the bug may have moved. Find the upstream change at the root-cause site (`git -C <project>/.fork log -p <old-tag>..<new-tag> -- <file>`).
  Only when that change is identified and explains the fix, set `state` to `fixed-upstream` and `fixed_upstream` = `{"version", "commit"}`.
  If no such change is found, keep `no-longer-reproduces` and say so: the vulnerability stays on the watch list for the next release.
- A **different** crash at the same site is a candidate new finding for `/fuzzing:fuzz-run` triage, not a replay result.

## 3. Rebuild the patch branch
```bash
git -C N/src switch -c patched/<new-version>                         # from fuzzing/<new-version>
```
For each root cause that still reproduces, apply its fix (cherry-pick it from `patched/<old-version>`, or `git apply` the vulnerability's `fix.patch` and commit).
Shared root causes stay one commit. A fix that no longer applies is re-done against the new source and tested as in `/fuzzing:vuln-report`
§2 (PoCs, saved inputs, seeds byte-identical); export it again to `fix.patch` only for vulnerabilities whose report is not yet sent — a
sent report keeps its patch, the new one is recorded in `status.json` (`fix`, `fix_commit`). Update `fix_commit` in every affected `status.json`.
Rebuild every campaign on `patched/<new-version>` and replay again: every still-reproducing input must now pass.

## 4. Retire the old version
- Delete `patched/<old-version>` (`git -C <project>/.fork branch -D patched/<old-version>`; it was temporary) and, once nothing runs on it, the old
  worktree (`git -C <project>/.fork worktree remove O/src`). `fuzzing/<old-version>` stays: it records the campaigns as they were for that release.
- `O/` (reports, findings, status) stays in the workspace repository unchanged except for `status.json` updates.

## 5. Continue fuzzing
Start the campaigns on `N/src` (branch `patched/<new-version>`) with `/fuzzing:fuzz-run <project>/<new-version>/<campaign>`. Seed each from its
old corpus (`afl-cmin` of the last round's queue, see `/fuzzing:fuzz-run` §6) so coverage carries over.

End with a table: vulnerability, old state → new state, evidence (replay counts, upstream commit if found), and whether its fix is on `patched/<new-version>`.
Commit the workspace changes (`status.json`) with that table in the message. Push nothing unless the user asks.
