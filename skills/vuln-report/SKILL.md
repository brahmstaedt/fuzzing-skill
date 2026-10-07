---
name: vuln-report
description: Write vulnerability reports from fuzzing findings — one report per finding with summary, evidence level, reproduction, CVSS 3.1 scoring with justification, demonstrated vs maximum impact, tested patch, mitigation and regression test, plus a separate internal disclosure-handling file (not a report section). Use for "write vulnerability reports", "CVSS for this crash", "prepare a disclosure", "write a patch for this finding", after /fuzzing:fuzz-run produced curated findings.
---

# Vulnerability reports from fuzzing findings

`/fuzzing:vuln-report <project>/<version>` — paths are relative to the workspace (`$FUZZ_ROOT` or the current directory; layout in
`/fuzzing:fuzz-campaign`), `V` = `<project>/<version>`. Input: the curated findings of every campaign of this version (`V/<campaign>/findings/`:
README.md, SUMMARY.md, crash folders, `minimized/`), the problems logs `V/<campaign>/problems.md`, the triage tools and results in `V/triage/`,
and the source in `V/src/` (branch `fuzzing/<version>` = the pristine release plus our campaigns). Output, all in `V/`:

```
V/
  INDEX.md                  index only: one table of internal id (VULN-NN) with links to report, fix.patch, poc/, status.json. No facts of its own (no CVSS, state, disclosure, root cause) — a copy here goes stale
  VULN-NN-<slug>/           one folder per vulnerability (a vulnerability = one root cause; list merged signatures inside the report)
    report.md               the report (what the maintainer receives)
    fix.patch               exported fix: `git format-patch -1 --stdout <fix commit>`; links from the report, never inlined
    status.json             machine-readable state (§5)
    poc/                    PoC inputs + sha256sums + generator + replay script
  repro/                    standalone reproducer shared by the reports of this version: source + build.sh + README + SHA256SUMS — only when no shipped tool reaches the path (§1 item 6)
  disclosure_<date>.md      internal disclosure plan and log for one sending (§6); a proposal until sent, NEVER part of a report
```

**Fixes live in the fork, one commit per root cause.** Create the temporary branch `patched/<version>` from `fuzzing/<version>` in `V/src`
(`git -C V/src switch -c patched/<version>`; later fixes are added on top) and commit each fix there — **one commit per root cause**, not per
report: two reports that share a cause share one commit, and their `fix.patch` files are the same export. Build and test on that branch
(`V/src/fuzz/*/build.sh` builds whatever is checked out); `git diff fuzzing/<version> patched/<version>` is the change against the pristine
release. Never commit fixes to `fuzzing/<version>`, and never push `patched/<version>` anywhere public: the fuzzing campaigns continue on it
(`/fuzzing:fuzz-run` §6) until `/fuzzing:fuzz-rebase` replaces it on the next upstream release.
Reports contain working crash inputs: mark every report **CONFIDENTIAL – coordinated disclosure**, keep them in the private workspace, do not publish, upload or send them anywhere
(no vendor contact, no CVE request, no ticket) unless the user asks; propose those steps in `disclosure_<date>.md` (see below), never in the report. Never invent CVE numbers, advisory IDs or vendor responses.

**Disclosure handling is not part of the report.** Contact channel, proposed timeline, embargo, CVE strategy, credit, PoC handling and which findings to bundle are decisions *we / the user* make before anything is sent — the maintainer who receives the report is the vendor and acts on none of it. Collect them in one file per **sending** for the target version, `V/disclosure_<date>.md` (shared across the findings sent together — these facts do not vary per finding), marked *proposal — nothing sent* until it is (§6); look the contact channel up from the project's SECURITY/contact page, do not guess. The report body carries only the CONFIDENTIAL marker above and **no disclosure section** (§3b: the report is the maintainer's artifact, not our playbook).

## 0. Group the findings
One report per **root cause**, not per ASan signature. Several signatures that share a cause (e.g. a read overflow, a SEGV and a use-after-free that all come from one bad index)
go into one report as "manifestations". Keep a separate report for a different cause or class (e.g. resource amplification).

**A finding that does not become a report.** Not every crash folder is a vulnerability. If a crash cannot be reproduced at all (not even under the harness), is a sanitizer-only artefact with no consequence on a normal build (e.g. a leak-on-exit or a benign unaligned access that the plain build and Valgrind both shrug off), or on inspection is not a defect, do **not** write a report or invent a reproduction to fill the slot. Record it in the campaign's `problems.md` (what you tried, why it is benign/non-reproducible) and list it in INDEX.md as *not reproduced*, linking the problems log instead of a report. Only genuine, reproduced root causes get a `VULN-NN-<slug>/`.

## 1. Report structure (every report, in this order)
**Write for the one audience that acts on it: a maintainer of the target project** who has a normal clone at a released version and the project's own tools — and none of our fuzzing setup (no harness, no sanitizer-only build, none of our workspace: campaign dirs, `work/`, findings, triage tools or scripts). Every section must be usable from their checkout alone. The full expectation is in **§4.0** (it is the same bar the verifier later checks against — meet it now rather than discovering it in review). In particular: the reproduction must not depend on our harness, the plain-build symptom must be stated, the input must have a project-tool dump, the root cause must be a public-API call path at a named version, and the regression test must be adoptable by their suite. Build these in as you write; do not leave them for the verifier.

1. **Header**: descriptive title, date, status (`draft`), classification (CONFIDENTIAL), author, product + exact version + component (file:function), environment (OS, compiler, flags), the tested release tag and commit (plus the sha256 of the release tarball if the project ships one), a **method & provenance** line, and a placeholder for the **upstream identifier** (issue / advisory / CVE) the project assigns once filed. Do **not** put our internal `VULN-NN` number in the report (§3b) — it lives only in the filename and INDEX.md.
   - **Method & provenance** (one line, always): state that the finding was discovered by **fuzzing** — name the fuzzer (e.g. AFL++) and the harness/target path it exercised — and that **this report was drafted with AI assistance from the fuzzing campaign's findings and then verified** (point at the evidence/reproduction/verification sections). Maintainers weigh an AI-drafted report differently and should be told plainly; it is an honesty requirement (§3), not marketing.
2. **Summary** (5-8 lines, no jargon; heading is "Summary", not "Management summary" — most OSS projects have no management tier): what is wrong, who is exposed, worst case, how hard to exploit, severity (CVSS), what to do (patch available + tested?), how sure we are.
3. **Evidence level** — a table that separates *verified* (reproduced, and how: ASan / plain build / Valgrind / trace), *reasoned* (derived from code or from measurements, not executed) and *not tested*. Every later claim must be traceable to a row.
4. **Technical description**: class + CWE, root cause at code level with the relevant source excerpt (file:line), why the bug appears (the data flow from the input byte to the faulty access), why it differs between sanitizer and plain builds if it does.
5. **Attack scenario**: attack vector (network / local / file), preconditions, who is exposed (every application that parses untrusted input with this API), required user interaction, privileges. State what the harness does that a real application also does.
6. **How to reproduce** — must work from a maintainer's checkout **without our harness** (§4.0). Give: the upstream build they would actually use (release tag/tarball + real cmake/configure flags); a reproduction driven by **either a project-shipped executable** (named, exact args — e.g. the renderer/CLI that reaches the path) **or a minimal standalone program whose full source ships in the report** (`V/repro/`, shared by the reports of this version and linked as `../repro/`: a standalone source in the target's own language — `.c` or `.cc` — plus `build.sh` + `README`, public API only, one-line compile), with the AFL/sanitizer harness mentioned only as *additional* evidence labelled as ours; the PoC file(s) with sha256 and the *expected output* for each verified variant — and if a PoC was minimised, **re-triage it and confirm it still shows the same ASan error class at the same frame as the finding it illustrates** (`afl-tmin` can change the error class; see fuzz-run §5c), shipping the un-minimised original when the class matters, and **leading with the smallest, most *reliable* variant** rather than the most dramatic one (§3a) — **leading with the plain non-sanitizer build's symptom** (segfault / silent / wrong output / only-under-Valgrind) since that is what they run first; and a minimal-input description dumped by the **project's own** structure tool. If the stream/in-memory path and the file-open path differ (e.g. a library that loads large values lazily from a seekable file but eagerly from a stream), say which PoC covers which — do not assume one reproduces the other.
7. **Impact**: two separate parts. *Demonstrated*: what was actually observed (crash, an out-of-bounds write of N bytes, a wild read, peak memory). *Maximum plausible if exploited*: what an attacker could achieve under stated assumptions (information disclosure, code execution, denial of service), clearly labelled as not demonstrated, with what would be needed (heap layout control, ASLR bypass, …). Do not claim code execution without a demonstrated primitive; say "could not be assessed" when true.
8. **CVSS v3.1**: vector string, base score (compute it, do not guess — use the formula or a script, print the intermediate values), and one sentence per metric (AV, AC, PR, UI, S, C, I, A) justifying it. Mention the realistic range (e.g. AV:L vs AV:N depends on the application) and the alternative score if an assumption changes. Add the temporal note: PoC available, no known fix upstream (unless checked).
9. **Affected versions**: tested version(s); others "not tested" unless checked; how to check (diff of the function in neighbouring releases) — only claim what was looked at.
10. **Mitigation / workaround** for users who cannot patch (input checks an application can do before handing data to the library, running the parser sandboxed with memory limits, process isolation) and **detection** hints.
11. **Patch**: a link to `fix.patch` (do not inline the diff — it drifts from the file), the reasoning for each hunk, the layer you chose to fix at and the alternatives you considered (a maintainer may prefer a different layer), what it deliberately does not change, and the **test record**: build of the patched tree, every PoC no longer fails (ASan build AND plain build + Valgrind), the full saved set of inputs for this finding no longer fails (counts), normal seeds produce byte-identical output before/after (render to a buffer, compare hashes), no new failures on the corpus sample. If a part is untested, say so. A patch that does not pass its own reproducers is not a patch, report it as a "proposed direction".
12. **Regression test** the vendor can **drop into their own suite**: a tiny input (or a generator) + the expected result, expressed for their test framework or as a standalone using public API + the shipped input — not "re-run our harness". Include the variant that catches a naive fix regressing (e.g. the boundary value next to the crashing one), not only the headline crash.
13. **Limitations and open questions**: everything not established; what would settle it.
14. **References**: CWE links, the relevant source locations, upstream issue tracker search result (searched / not searched).

(Disclosure is deliberately **not** a report section — it goes in `disclosure_<date>.md`; see "Disclosure handling is not part of the report" above.)

## 2. How to get the facts (do this, do not recall)
- **CVSS**: compute with a small script (v3.1 formula: ISS, impact, exploitability, round-up) and print the numbers into the report; cross-check the vector string format `CVSS:3.1/AV:./AC:./PR:./UI:./S:./C:./I:./A:.`.
- **Root cause**: read the source at the sites named by ASan; confirm mechanisms by tracing in a *throwaway worktree* (`git -C <project>/.fork worktree add --detach V/work/trace fuzzing/<version>`; `fprintf` at the suspected spot, build `-O0`, run the minimised PoC and a normal seed as control) — hypotheses stay labelled "reasoned" until traced.
- **Affected versions**: check out neighbouring release tags and the upstream default branch in `<project>/.fork` (`git fetch upstream --tags`) and diff the function; only claim "affected" if the code is identical or the PoC was run.
- **Patch**: smallest change that removes the root cause; keep behaviour for valid input identical. Commit it on `patched/<version>` (one commit per root cause), rebuild the plain+ASan variants from that branch, run (a) PoCs, (b) the whole saved finding set, (c) all seeds with output hash comparison, (d) a sample of the fuzz corpus for new failures. Record counts.
- **Environment line**: compiler version, kernel, whether ASan fill bytes or zeroed pages explain differences.
- **Maximum impact** must follow from the code (type and size of the bad access, how controlled the offset/value is), not from the vulnerability class name.
- **Harness-free reproduction** (do it while writing, not in review): build the project the normal way — its own documented cmake/configure, **not** the `/fuzzing:fuzz-run` plain build (that one may still be harness/instrumentation-linked, so it does not establish the maintainer-facing symptom) — then make the crash happen through a project-shipped executable or a minimal standalone you write (`V/repro/`: public API only, reads the input file as an argument), and record the plain-build symptom. If a shipped tool reaches the path, prefer it; if none does, the standalone source is part of the deliverable. Check the stream vs file-open paths separately — deferred/partial loading can make a seekable-file open behave differently from an in-memory stream, so a PoC that crashes the harness may need a different (e.g. smaller) variant to crash a plain file open.

## 3. Quality bar and honesty rules
- Separate *verified / reasoned / not tested* everywhere; downgrade wording when evidence is thin ("candidate", "likely").
- State plainly what failed or could not be established (a hypothesis that tested negative goes in the report as such).
- Severity is a judgement: give the score and its assumptions, not a verdict. No marketing language, no exploit development beyond what is needed to show the bug.
- Cross-link: INDEX.md (our internal index) lists every vulnerability with its internal `VULN-NN` id and links to report, `fix.patch`, `poc/` and `status.json` — nothing else, so it never needs synchronising with the reports, status files or disclosure files; each report links `poc/` and `fix.patch`. The `VULN-NN` id stays in INDEX.md and filenames, never in the report body (§3b).
- Record every wrong turn or blocked step in the campaign's `problems.md` (as in `/fuzzing:fuzz-run`).
- End the final message with the list of reports, what is verified vs reasoned in one line each, and the open decisions for the user (disclosure, CVE, who contacts the vendor).

## 3a. Lead with the minimal, reliable trigger — not the fuzzer's dramatic artifact
A fuzzer ships *one* input out of the whole space that hits the bug, and it tends to land at an extreme — huge size fields, deep nesting, maximal counts — because that is where its mutations happened to be when the crash fired. That extreme is almost never the essence of the bug, and leading the report with it costs you on every axis. The headline rule is "smaller is better"; the substance is **report the bug, not the fuzzer's accident.** Concretely, the extreme input:
- **hides the root cause** — the dramatic number is a symptom at one corner of the trigger space; the mechanism is the small invariant that actually breaks (an off-by-one at a boundary, one unchecked field). Demonstrate the mechanism with the smallest input that still shows *the same error at the same frame*.
- **is often the *least* reliable reproducer** — if the crash first needs a resource to be acquired (a multi-GB allocation to *succeed*, an address-space size, a timeout), it is environment-dependent: on a constrained host the allocation fails and the target errors out cleanly *before* the bug, so the headline input "does not crash" for the maintainer. A variant with small values allocates trivially and crashes deterministically everywhere. **The most reliable PoC, not the most spectacular one, is the headline.**
- **invites mis-framing and copy errors** — a big incidental figure is easy to carry over from the wrong finding, to build a wrong narrative around ("the buffer was too short" when it was actually full-size), or to over-claim impact from. Small, essential numbers are checkable at a glance.
- **is not adoptable** — a maintainer can read a ~500-byte input in a hex dump, see the one bad field, and turn it into a regression test; a gigabyte-scale artifact they cannot.

So, for every finding:
1. **Separate essential trigger conditions from incidental ones.** Essential = what *must* hold for the bug (the code path, the boundary condition, the field that must be inconsistent). Incidental = what merely happened to be true in this crashing input (the gigabyte scale, a particular byte value). State the essential conditions explicitly in the technical description (report item 4); minimise the incidental ones away.
2. **Lead with the smallest, most *reliable* variant** as the primary PoC — the one whose crash does not depend on an environment-dependent resource — then **re-triage** to confirm it is still the same root cause at the same frame (§6; `afl-tmin` can drift the bug class). Minimising the *input file* is not the same as minimising the *trigger*: shrink the semantic value that makes it reliable and general (e.g. a smaller size field, shallower nesting), not just the byte count.
3. **Do not discard a high-impact extreme — relocate it.** If the dramatic variant demonstrates *additional* impact (e.g. an oversized allocation is a resource-exhaustion angle), that is usually a *separate finding* (§0) or a clearly-labelled secondary case under Impact (§7), not the headline crash. Keep the evidence; move it to where it is actually about.
4. **Note the reliability precondition wherever an extreme survives.** If any shipped variant needs a large resource granted first, say so in reproduction (§6) and impact (§7) — "crash requires the N-byte allocation to succeed; on a memory-constrained host the parser errors out instead" — so no one scores or trusts it as unconditional.

## 3b. Write the report as the finished artifact, not a record of how we made it
The report is the document a project maintainer opens; it must read as a clean, first-and-final statement of the **current, correct** conclusions. *How* we arrived at them is not part of it. Our authoring and verification process — a revision changelog or §History section, "rev 1 said X, now corrected", "after two verification rounds", review-round narration, superseded numbers kept for comparison — is noise to the maintainer and lives **only** in our internal `problems.md` (the verifier reports back to the main agent directly and writes no file of its own), never in the report. If a fact changed during review, the report simply states the right fact; it does not narrate the correction.
- **Process history goes; engineering warnings stay (rephrased).** "An earlier *draft of our report* said the buffer was N bytes" is process — drop it. "The obvious one-line fix (clamping the length to what was read) turns the over-read into an over-write for some boundary inputs — do not use it" is a fact about a tempting wrong fix — keep it, as a property of *the code*, not of our drafts.
- **No internal identifiers in the report body.** Our `VULN-NN` numbering is a filename convention for *our* tree; it means nothing to the maintainer and must not appear as the report's ID, in its title, or in cross-references. Identify the report by its descriptive title, with a placeholder for the **upstream identifier** (issue / advisory / CVE) the project assigns once filed. Refer to a sibling finding by what it *is* ("the resource-exhaustion finding with the same root cause"), not "VULN-03". INDEX.md and filenames keep `VULN-NN` — that is our index, not the report.

General rule: before shipping, reread each section as the maintainer and strike anything that is about *us* — our process, our tree, our numbering, our tooling — rather than about *their bug and its fix*. The changelog and the internal ID are the two commonest instances; the dramatic artifact (§3a) and internal paths (§4.0) are others.

## 4. Independent verification loop (run after each report is drafted, before the final message)
A report is not finished until an independent check has tried to break it. Because the author is primed by the facts they just assembled, the check must be done by a **fresh subagent with no prior context** — it re-derives everything from the artefacts and source, and will catch exactly the mistakes the author cannot see (e.g. a number copied from the wrong input file, a root cause that does not match the shipped PoC, a patch that regresses on in-scope inputs).

Run this loop per report (`V/VULN-NN-<slug>/report.md`). **At most 3 review rounds.**

### 4.0 The verifier's stance: a developer on the receiving project
The verifier does the technical re-derivation below, but adopts a specific role while doing it: **a developer/maintainer of the target project who has just received this report** and must (1) understand it, (2) validate it against their own checkout, and (3) act on it (apply or judge the fix, add a regression test). They have a normal clone of the project at a released version and the project's own shipped tools — and **nothing** from our fuzzing setup: no AFL++ harness, no persistent-mode binary, no sanitizer-only build, none of our workspace (campaign dirs, `work/`, findings, triage tools) or scripts. At every step the verifier asks "could this developer get here from the report alone?" and flags whatever they could not.

Such a developer expects the report to be **self-contained against their checkout**:
- **A reproduction they can actually run.** Not "pipe the file into our harness" — they don't have it, and shipping it buries the bug in AFL/sanitizer scaffolding they'd have to reverse-engineer to trust. The report must reproduce the crash with either (a) **an executable the project already ships** (named, with exact arguments — e.g. the renderer/CLI that exercises the vulnerable path), or (b) if nothing shipped reaches the path, **a minimal standalone program whose full source is in `V/repro/`**, using only the project's public headers and libraries, with the exact compile command against a normal build. Writing that standalone reproducer is part of the report, not left to the reader. Our harness/sanitizer build may appear only as *additional* evidence, clearly labelled as ours.
- **The behaviour on a normal (non-sanitizer) build**, since that is what they build first: segfault / silent / wrong output / only-observable-under-Valgrind. A bug invisible without ASan must say so explicitly, or the developer builds a release, sees nothing, and closes it "cannot reproduce." Lead with the plain-build symptom; sanitizer output corroborates.
- **Upstream build instructions they'd really use:** the release tag/tarball and the actual cmake/configure flags — no dependency on our scripts.
- **The input in a tool-neutral form:** a dump from the *project's own* tooling (not ours) plus the fields that matter, and ideally a small generator so they can regenerate and vary it — not just an opaque binary.
- **Root cause pinned to their source** at a named version/commit, with the call path from a *public, documented* API entry point to the faulting line (the stack they'd breakpoint), and why valid inputs do not reach it.
- **A fix they can judge and a test they can adopt:** a diff against their tree (`patch -p1` / `git apply`) with per-hunk reasoning, the layer chosen *and alternatives considered* (a maintainer may prefer a different layer), evidence that valid inputs are byte-unchanged, and a regression test expressed for *their* test framework or as a standalone using public API + the shipped input — not "run our harness."
- **No leakage of our internal world:** paths into our workspace (campaign dirs, `work/`, findings, triage), our dashboards and build scripts are meaningless to them, and so are our *process* (verification-round narration, a revision changelog, "rev 1 said…") and our internal `VULN-NN` numbering; any internal path/script that is load-bearing for reproduction is a defect, and any process history or internal id in the report body is a defect regardless (§3b).
- **Every section earns its place for the maintainer.** Do not take the section list for granted: walk **each** report section and name *who acts on it*. Anything that serves *us* rather than the maintainer's understanding / fix / test is misplaced and belongs in an internal file, not the report — the commonest trap is a *disclosure-planning* section (vendor-contact channel, timeline, embargo, CVE strategy, PoC handling): the recipient **is** the vendor and acts on none of it, so it belongs in `disclosure_<date>.md`, never the report. This is a question about whether a section should exist at all — a different check from "is the document self-consistent", and the one most likely to be skipped because a conventional-looking heading reads as fine.

### 4.1 Spawn the verifier (no context)
Use the `Agent` tool with `subagent_type: "general-purpose"` (NOT `fork` — it must start clean). Give it only the report path and the project layout; do **not** hand it your conclusions, your reasoning, or what you think is correct. The prompt:

> Independently verify the vulnerability report at `V/VULN-NN-<slug>/report.md` (V = the given `<project>/<version>` in the workspace). Do not trust it. Reproduce its claims from scratch and check it for (a) **inaccurate statements** and (b) **incomplete / missing aspects**. Concretely:
> - Re-run the shipped PoC(s) on every build variant the report relies on (AFL++/ASan harness, plain `-O2`, plain ASan-only, Valgrind, patched build); confirm the signature, the faulting line, the allocation/region sizes and exit codes the report states — numbers must come from *the file the report ships*, not a neighbouring finding.
> - Read the named source (`V/src/`, branch `fuzzing/<version>` = pristine release; use `git show fuzzing/<version>:<path>` if the worktree is on `patched/<version>`) at the ASan sites and confirm the root cause and data flow actually match the shipped PoC (trace in a private copy if needed); check the illustrative code excerpts are the code at the cited lines.
> - Rebuild with the fix (`patched/<version>`, or `git apply fix.patch` on a throwaway worktree of `fuzzing/<version>`) and test it against the PoC, the full saved finding set, the seeds (output-hash equivalence) **and crafted in-scope variants** (e.g. truncated inputs and boundary cases) to see whether the fix is incomplete or introduces a new over-read/over-write or regression.
> - **Then take the role of a developer on the target project who just received this report** and has only a clean checkout at the released version plus the project's own shipped tools — none of this fuzzing workspace's harness, scripts, or internal trees (campaign dirs, `work/`, findings, triage). Reproduce the crash the way THEY would: build the project with its normal (non-sanitizer) build, and either run the project-shipped executable the report names (with its exact args) or compile and run the minimal standalone reproducer the report provides — explicitly **NOT** the AFL/sanitizer harness. Flag it as a failing requirement if: the only reproduction offered is our harness; the bug is ASan-only but the report does not say so (and state what a plain build actually does); no shipped tool reaches the path and no standalone reproducer source is included; the input has no project-tool dump / generator; the root cause is not pinned to a public-API call path at a named version; there is no regression test the project could adopt; any internal path/script of ours is load-bearing for understanding or reproduction; the report carries our internal process (a changelog / revision history / verification-round narration) or our internal `VULN-NN` numbering instead of a descriptive title plus an upstream-identifier placeholder (§3b); or any report section serves *us* rather than the maintainer — **walk every section and name who acts on it**, and flag disclosure / vendor-contact / CVE / embargo *planning*, campaign bookkeeping, or any "steps before sending", which belong in `disclosure_<date>.md` or `problems.md`, not the report. (See §4.0 for the full expectation.)
> - Check CVSS vector/score arithmetic, affected-version claims, and that every "verified" row is actually reproducible.
> Report your findings **directly back to the main agent — do not write any file**. Give: inaccuracies first, then gaps, each with the command/output that shows it, most-severe first; then a short structured summary — VERDICT (sound / needs-fixes / unsound), then a list of issues each tagged [inaccuracy|gap] + [major|minor] with a one-line fix. Verify, do not edit the report.

Spawn one verifier per report; independent reports can be verified in parallel (separate `Agent` calls in one message).

### 4.2 Act on the result (main agent keeps control)
The main agent (with full context) owns the fix decision — never let the verifier edit the report.
- **No major issues** (verdict sound, only minor/accepted gaps): record the pass (in the final-message review outcome, §4.3) and move on. Done for that report.
- **Issues to fix**: apply the corrections to the report (and patch/PoC if implicated), keeping the verified/reasoned/not-tested discipline. Weigh each finding — a verifier can be wrong too; if you reject one, record why in the campaign's `problems.md` (not in the report — that is process history, §3b) rather than silently ignoring it. Acting on a §4.0 (developer-usability) gap often means *producing a missing artefact*, not just editing prose — e.g. writing and testing the minimal standalone reproducer, adding the project-tool dump/generator, or building the project the normal way to record the plain-build symptom — then referencing it from the report.
- After fixing, **decide whether another round is warranted**: order a re-review only if the fixes were substantive enough that new mistakes could have crept in (e.g. a rewritten root cause, a changed patch). Cosmetic fixes do not need another round. Each new round is a **fresh no-context subagent** (preferred — re-spawn for a genuinely independent re-derivation, especially after a changed root cause or patch); only to recheck a verifier's *own* prior points may you instead continue that same agent via `SendMessage`.

### 4.3 The 3-round cap
Count every verification pass on a report toward its cap of **3**. If after 3 rounds the report still has unresolved **major** issues (the verifier and the fixes have not converged — e.g. the patch keeps regressing, or a root cause cannot be pinned down), **stop** and report that to the user explicitly: name the report, the specific points that did not settle, what each round changed, and what you believe is needed to resolve it. Do not keep looping, and do not paper over an unsettled report as finished. A report that passes earlier settles earlier — the cap is a ceiling, not a target.

Note the review outcome for each report in the final message (passed in N rounds / unsettled after 3) alongside the verified-vs-reasoned line.

## 5. status.json — the state of one vulnerability
Every `VULN-NN-<slug>/` has a `status.json`; it is the only place that records state, so INDEX.md, the reports and the disclosure files never
have to be kept in sync with it. A vulnerability stays in the folder of the version where it was found; later releases only append replays.

```json
{
  "id": "VULN-01",
  "target": "<project>",
  "found_in": "<version>",
  "found_by": ["<campaign>"],
  "state": "open",
  "reported": null,
  "upstream_id": {"ticket": null, "cve": null},
  "vendor_verdict": null,
  "root_cause": "<one line; name shared causes, e.g. 'A: ...', so reports with one fix are recognisable>",
  "fix": "<what fix.patch does and does not cover>",
  "fix_commit": "patched/<version> <sha> (root cause A)",
  "replays": [
    {"version": "<version>", "date": "YYYY-MM-DD", "asan": "<class @ frame>", "plain": "<plain-build symptom>", "inputs": 0, "reproduced": 0}
  ],
  "fixed_upstream": null
}
```

- `state`: `open` → `reported` (sent; `reported` = `{"date", "ref"}` with the disclosure tag) → `no-longer-reproduces` or `fixed-upstream`; `duplicate` when the vendor already knew it.
  `no-longer-reproduces` = every saved input passes under ASan and the plain build on a newer release. `fixed-upstream` additionally needs the upstream
  change at the root-cause site identified (`fixed_upstream` = `{"version", "commit"}`); only that closes a vulnerability.
- `fix_commit` points into a temporary branch and changes when it is rebuilt; `fix.patch` is the lasting form.
- Record vendor facts only as received (ticket ids, verdicts, scores they give). Never invent CVE numbers or vendor responses.

## 6. Sending a disclosure
Only when the user decides to send, and only what they approve:
1. Build the package from the reports, `fix.patch` files, `poc/` and `repro/` — never INDEX.md, `status.json`, `disclosure_*.md`, findings or triage.
2. Commit the reports exactly as sent and tag that commit in the workspace repository: `git tag -a disclosure_<date> -m "<what was sent, to whom>"`. The tag is the
   record of what the vendor received; nothing is changed in it later. Name the tag in `disclosure_<date>.md` and the file in the tag message, so each leads to the other.
3. Set `state` to `reported` in each `status.json` (with the tag as `ref`) and log the sending in `disclosure_<date>.md`.
4. Corrections after sending (vendor feedback, errata) are new commits after the tag: an *Errata* section in the report, updated `status.json`.
   `git diff -M disclosure_<date> HEAD -- <project>/<version>` shows everything that changed since the sending. A later sending gets its own
   `disclosure_<date>.md` and tag.
