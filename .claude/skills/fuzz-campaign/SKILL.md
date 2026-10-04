---
name: fuzz-campaign
description: Prepare an AFL++ fuzzing campaign for a C/C++ library — builds the library with instrumentation, writes a fuzzing harness for a stated goal (e.g. "fuzz the DICOM input parsing"), generates seed inputs, and produces one runnable binary plus a seeds directory. Use for "fuzz this library", "write a fuzzing harness", "AFL++ harness", "set up fuzzing for <library>".
---

# Preparing a fuzzing campaign

The user names a library, a version and a goal — `/fuzz-campaign <library> <version> <goal>`.
If `notes/<name>.md` exists, read it first: it holds known quirks for that library.
Produce, under `campaigns/<name>/` (`<name>` = `<library>-<version>`):

```
campaigns/<name>/
  build.sh        builds library + harness; the only script, kept short
  harness.cc      what gets fuzzed (harness.c for C)
  seeds/          a handful of small valid inputs
  harness         the result: AFL++-instrumented binary (ASan); fuzzed by AFL++, replayed with: ./harness < input
```

The user starts fuzzing themselves, on their own machine, with one `afl-fuzz` command. Do not
start it. There is no framework: write plain commands, keep `build.sh` under ~40 lines, put a
comment on every line that is a decision rather than boilerplate.

**Problems log.** The moment something fails, is blocked, or a first attempt turns out wrong
(build error, wrong flag, dead end, unverified step, a step you skipped), append one line to
`notes/<name>.problems.md` *before* continuing, so the log can be followed live:

```bash
echo "- $(date +%H:%M) [generic|<library>] what failed -> what fixed it (verified|unverified|unresolved)" >> notes/<name>.problems.md
```

Tag `generic` if it would hit any library, otherwise the library name. Log wrong turns too, not
only the final fix. Log problems only, never what the fuzzer finds or where a bug might be.
Never read this file as input; `notes/<name>.md` is the curated one.

## 1. Build the library, instrumented

First check the toolchain is present:

```bash
command -v afl-fuzz clang-17 cmake gdb >/dev/null || \
  { sudo apt-get update -qq && sudo apt-get install -y -qq afl++ clang-17 libclang-rt-17-dev lld-17 llvm-17 cmake build-essential pkg-config wget gdb; }
```

Download the exact release tarball into `work/cache/` (reuse it if present), unpack under
`work/src/`, and build **static** libraries with the AFL++ compiler and AddressSanitizer:

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
compressed image, not only an uncompressed one). Put any generator program and its binary outside `campaigns/<name>/` (e.g. `work/`), so that
only the four listed items stay there. Prefer generating them with the library's own
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

## 5. Hand back

Report: entry point chosen, options and why, seed count. Then print the command for the user to run
**on their host**:

```bash
ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:symbolize=0 \
  afl-fuzz -i campaigns/<name>/seeds -o out -m none -- campaigns/<name>/harness
```

and the replay command for later, which prints the AddressSanitizer report with a stack trace:

```bash
ASAN_OPTIONS=detect_leaks=0:symbolize=1 campaigns/<name>/harness < out/default/crashes/id:000000*
```

End the final message with one line telling the user what to do next, in these words: "Next: run
the afl-fuzz command above on your host from the project root; it stops with Ctrl-C." Nothing
after it.

## Gotchas that have cost real time

- AFL++ refuses to start if `/proc/sys/kernel/core_pattern` begins with `|`; fix on the host:
  `sudo sysctl -w kernel.core_pattern=core`.
- ASan needs `-m none` (it reserves enormous address space) and `detect_leaks=0` (error paths
  leak; otherwise every leak is a "crash").
- Formats with declared sizes: add `malloc_limit_mb=…` to `ASAN_OPTIONS` or a fuzzed header
  claiming a huge image will exhaust memory.
- A script that deliberately runs a crashing binary under `set -e -o pipefail` needs `|| true`.
