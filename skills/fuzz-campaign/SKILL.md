---
name: fuzz-campaign
description: Prepare an AFL++ fuzzing campaign for a C/C++ library — builds the library with instrumentation, writes a fuzzing harness for a stated goal (e.g. "fuzz the input/file parsing"), generates seed inputs, and produces one runnable binary plus a seeds directory. Use for "fuzz this library", "write a fuzzing harness", "AFL++ harness", "set up fuzzing for <library>".
---

# Preparing a fuzzing campaign

`/fuzzing:fuzz-campaign <project> <version> <campaign> <goal>` — e.g. `libpng 1.6.43 png-decode fuzz the PNG decoding`.
`<campaign>` is a short goal slug; it names the campaign across versions (the same harness is carried to the next release by `/fuzzing:fuzz-rebase`).

## Workspace layout (shared by all fuzzing skills)

Everything lives under the workspace: `$FUZZ_ROOT`, or the current directory. Its name does not matter and is never written into anything.

```
<project>/
  .fork/                     clone of the project (remotes: upstream = the project, push disabled; origin = the user's private fork, if any)
  <version>/
    src/                     git worktree of .fork: branch fuzzing/<version> (pristine + campaigns) or patched/<version> (known findings fixed)
      fuzz/<campaign>/       build.sh, harness.cc, seeds/, seed generator, dictionary, NOTES.md; harness (built, never committed)
    work/                    scratch: library builds, plain/trace builds, generator binaries (never committed);
                             work/pristine/ = second worktree at fuzzing/<version> for the unpatched triage build (fuzz-run §6)
    <campaign>/out/          raw AFL++ output (never committed); earlier rounds in out/archive/roundN
    <campaign>/findings/     curated inputs per signature + minimized/ + README.md (from /fuzzing:fuzz-run)
    <campaign>/problems.md   problems log (below)
    VULN-NN-<slug>/  repro/  triage/  INDEX.md  disclosure_<date>.md      (from /fuzzing:vuln-report)
```

Only the campaign sources (on the fork branch) and the curated security material (in the workspace) are ever committed; `src/`, `work/` and `out/` are not part of the workspace repository.

Produce, under `<project>/<version>/src/fuzz/<campaign>/`:

```
build.sh        builds the checked-out library + harness + seeds; the only script, kept short
harness.cc      what gets fuzzed (harness.c for C)
seeds/          a handful of small valid inputs, plus the generator source if you wrote one
NOTES.md        curated build/harness quirks for this library (create or carry over; see below)
harness         the result: AFL++-instrumented binary (ASan); fuzzed by AFL++, replayed with: ./harness < input
```

The user starts fuzzing themselves, on their own machine, with one `afl-fuzz` command (or with `/fuzzing:fuzz-run`). Do not
start it. There is no framework: write plain commands, keep `build.sh` under ~40 lines, put a
comment on every line that is a decision rather than boilerplate.

**Known quirks first.** Earlier campaigns for the same project keep curated quirks in `fuzz/<campaign>/NOTES.md` on their
branches. Read them before building: `git -C <project>/.fork log --all --format='%h %D' -- 'fuzz/*/NOTES.md'`, then `git show <branch>:fuzz/<campaign>/NOTES.md`.

**Problems log.** The moment something fails, is blocked, or a first attempt turns out wrong
(build error, wrong flag, dead end, unverified step, a step you skipped), append one line to
`<project>/<version>/<campaign>/problems.md` *before* continuing, so the log can be followed live:

```bash
echo "- $(date +%H:%M) [generic|<project>] what failed -> what fixed it (verified|unverified|unresolved)" >> <project>/<version>/<campaign>/problems.md
```

Tag `generic` if it would hit any library, otherwise the project name. Log wrong turns too, not
only the final fix. Log problems only, never what the fuzzer finds or where a bug might be.
Never read this file as input. At the end, curate the `[<project>]` lines that a future build needs into `NOTES.md`;
`[generic]` lines are candidates for this skill itself — list them for the user, do not edit the skill unasked.

## 0. Source checkout

Fuzz the project's own repository at the release tag, not a tarball, so fixes and later releases are plain git operations:

```bash
[ -d <project>/.fork ] || { git clone --origin upstream <upstream-url> <project>/.fork &&
  git -C <project>/.fork remote set-url --push upstream DISABLED-never-push-to-upstream; }   # nothing ever goes upstream by accident
grep -qx 'fuzz/\*/harness' <project>/.fork/.git/info/exclude || echo 'fuzz/*/harness' >> <project>/.fork/.git/info/exclude
git -C <project>/.fork worktree add -b fuzzing/<version> ../<version>/src <release-tag>   # if <version>/src does not exist yet
```

If the user has a private fork, add it as `origin` (`git -C <project>/.fork remote add origin <url>`); push only when the user asks.
Find the release tag with `git -C <project>/.fork tag -l '*<version>*'`. If `fuzzing/<version>` already exists (another campaign), use it:
all campaigns of a version share one branch and one worktree.

## 1. Build the library, instrumented

First check the toolchain is present:

```bash
command -v afl-fuzz clang-17 cmake gdb >/dev/null || \
  { sudo apt-get update -qq && sudo apt-get install -y -qq afl++ clang-17 libclang-rt-17-dev lld-17 llvm-17 cmake build-essential pkg-config wget gdb; }
```

Build the checked-out tree (`src/`) out of source into `<version>/work/build-afl/` — **static** libraries with the AFL++ compiler and AddressSanitizer:

```bash
export AFL_USE_ASAN=1
export AFL_CC=clang-17 AFL_CXX=clang++-17       # AFL++ package is built against LLVM 17
CC=afl-clang-lto CXX=afl-clang-lto++ cmake ... / ./configure ...
```

- Drop optional dependencies that are not under test (compression, network, bindings), and
  build only the modules the harness links. Check whether the module list is a cache
  variable before patching any build file. Disable optional dependencies one at a time:
  switching one off can break the build, and auto-detected ones (see `CMakeCache.txt`)
  can break the static link.
- **Old code does not compile with a modern compiler.** Add the flags up front: C before
  ~2018 `-Wno-error=implicit-function-declaration -Wno-error=int-conversion
  -Wno-error=incompatible-pointer-types`; C++ before ~2018 `-Wno-c++11-narrowing`.
- Locate the built archives with `find build -name '*.a'` before writing the link line.
- `afl-clang-lto` needs `llvm-ar-17` / `llvm-ranlib-17` for static archives
  (`-DCMAKE_AR=/usr/bin/llvm-ar-17 -DCMAKE_RANLIB=/usr/bin/llvm-ranlib-17`), and
  `AFL_CXX` must be the C++ driver (`clang++-17`), or every C++ link fails with undefined `std::`.
- If the library loads a data table or dictionary from disk at startup, find the build option
  that compiles it in. Otherwise the fuzzer explores a crippled parser without telling you.

## 2. Write the harness

Read the public headers first. Then make the decision that matters most:

> **Parsing is usually not enough.** Reading a file's structure often never reaches the code
> where the interesting bugs live. Go on to *do something* with what was parsed — decode the
> image, evaluate the expression, render the page. Ask what a real application does with this
> file after opening it, and do that.

Use AFL++ persistent mode so it is fast, reading the test case from the shared buffer:

```c
__AFL_FUZZ_INIT();
int main(void) {
    library_init(); register_codecs(); silence_errors();   /* once, outside the loop */
    __AFL_INIT();
    unsigned char *data = __AFL_FUZZ_TESTCASE_BUF;
    while (__AFL_LOOP(1000)) {
        int len = __AFL_FUZZ_TESTCASE_LEN;
        /* parse data,len → process the result → free everything */
    }
}
```

Run outside AFL++, the same binary reads **one input from stdin** and exits — that is how a
crash is replayed later (`./harness < crash-file`). It does *not* take a file argument: it would
silently wait on stdin forever. Do not add `@@` to the `afl-fuzz` command either; the persistent
loop receives the input through shared memory.

Rules:
1. **Go past parsing into processing.**
2. **Choose options deliberately and comment why** — a realistic, careful-application
   configuration, never one chosen by knowing where a bug is.
3. **Disable anything that touches the network** (external entities, URL loading).
4. **Silence error output** — malformed input is the normal case.
5. **Leave no state between iterations** — destructors run each loop; nothing global survives.

## 3. Write seeds

Six to ten small, **valid** inputs in `seeds/`, each under a few hundred bytes if the format
allows. Cover distinct constructs, including the one the processing step consumes (a
compressed image, not only an uncompressed one). Commit a generator's source next to the seeds; its binary goes to
`<version>/work/`. Prefer generating them with the library's own
tools. Keep them generic: seeds shaped like a known bug turn the search into a lookup.

## 4. Check the binary before handing it back

AFL++ will happily fuzz a useless harness for an hour. Spend a few seconds first:

```bash
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:symbolize=0
for f in seeds/*; do timeout 10 ./harness < "$f" || echo "seed crashes harness: $f"; done   # all must pass
# (always use `< file` and `timeout`: without redirected stdin the binary waits for the terminal)
AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 AFL_NO_UI=1 timeout 20 afl-fuzz -i seeds -o /tmp/check -m none -- ./harness
```

In the status output, require "stability" near 100% and a corpus that grows beyond the seed
count within 20 seconds. Low stability means state leaks between iterations; no growth means the
harness is not passing the input through or stops at parsing. Fix and repeat.

## 5. Commit and hand back

Commit `fuzz/<campaign>/` (not the `harness` binary) on `fuzzing/<version>` in the worktree, with a message that says what the
harness exercises. Do not push. The campaign must not encode the workspace: `build.sh` locates everything relative to itself.

Report: entry point chosen, options and why, seed count. Then print the command for the user to run
**on their host**, from the workspace:

```bash
ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:symbolize=0 \
  afl-fuzz -i <project>/<version>/src/fuzz/<campaign>/seeds -o <project>/<version>/<campaign>/out -m none -- <project>/<version>/src/fuzz/<campaign>/harness
```

and the replay command for later, which prints the AddressSanitizer report with a stack trace:

```bash
ASAN_OPTIONS=detect_leaks=0:symbolize=1 <project>/<version>/src/fuzz/<campaign>/harness < <project>/<version>/<campaign>/out/default/crashes/id:000000*
```

End the final message with one line telling the user what to do next, in these words: "Next: run
the afl-fuzz command above on your host from the workspace, or /fuzzing:fuzz-run <project>/<version>/<campaign>; afl-fuzz stops with Ctrl-C." Nothing
after it.

## Gotchas that have cost real time

- AFL++ refuses to start if `/proc/sys/kernel/core_pattern` begins with `|`; fix on the host:
  `sudo sysctl -w kernel.core_pattern=core`.
- ASan needs `-m none` (it reserves enormous address space) and `detect_leaks=0` (error paths
  leak; otherwise every leak is a "crash").
- Formats with declared sizes: add `malloc_limit_mb=…` to `ASAN_OPTIONS` or a fuzzed header
  claiming a huge image will exhaust memory.
- A script that deliberately runs a crashing binary under `set -e -o pipefail` needs `|| true`.
