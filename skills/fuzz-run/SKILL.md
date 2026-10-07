---
name: fuzz-run
description: Run and watch a prepared AFL++ campaign (from /fuzzing:fuzz-campaign) — start it on 80% of the cores with output on the host (or on a remote server, kept in sync locally), keep a dashboard refreshed every minute, give regular console status reports of real findings, and triage crashes vs. hangs. Also builds the Docker image for the campaign. Use for "start the fuzzer", "run the campaign", "status of the fuzzing", "are these hangs real", "dockerize the campaign".
---

# Running and watching a fuzzing campaign

`/fuzzing:fuzz-run <project>/<version>/<campaign>` — the campaign id; all paths below are relative to the workspace (`$FUZZ_ROOT` or the
current directory; layout in `/fuzzing:fuzz-campaign`). Shorthands: `V` = `<project>/<version>`, `C` = `<campaign>`;
campaign sources and the built harness are in `V/src/fuzz/C/`, AFL++ output in `V/C/out/`, curated findings in `V/C/findings/`.
Log every failure or wrong turn to `V/C/problems.md` as in `/fuzzing:fuzz-campaign`.

## 1. Preconditions (check, don't assume)
- `V/src/fuzz/C/harness` exists and replays the seeds (`./harness < seed`, with `timeout`).
- `/proc/sys/kernel/core_pattern` must not start with `|` (container and host share it; AFL++ aborts otherwise):
  `sudo sysctl -w kernel.core_pattern=core` on the host.
- Run the fuzzer **as the normal user**, never root: a root run makes the output dir root-owned mode 0700 and nobody can read it.
  If it already is: `sudo chown -R $USER V/C/out`.
- Memory: ASan harnesses grow to 1+ GB RSS each in persistent mode, and for formats with declared sizes a single input can push a process to several GB (no swap = desktop freeze / OOM kills).
  Averages mislead: size the instance count on the peak (watch `ps -eo rss,comm --sort=-rss`), and cut instances when `avail` drops under 2 GB. `ASAN_OPTIONS=malloc_limit_mb=…:allocator_may_return_null=1`
  did not tame such inputs in practice (cost was not one big malloc), so do not rely on it. Measure with a handful of the saved slow inputs before trusting any limit. Budget `instances × ~1.5 GB` against free RAM
  (`free -m`); if the box is short, use fewer instances or lower the harness's input-size cap. A kernel OOM kill shows up as a
  crash file named `…sig:09…` that does **not** reproduce — it is not a finding.

## 2. Start (native, in the background)
Output goes to `V/C/out/`, so campaigns and versions run side by side:
```bash
mkdir -p V/C/out
FUZZ_SNAPSHOT=${CLAUDE_SKILL_DIR}/scripts/session-snapshot.py \
  CAMPAIGN=$PWD/V/src/fuzz/C OUT=$PWD/V/C/out setsid nohup ${CLAUDE_SKILL_DIR}/scripts/fuzz-entrypoint.sh >> V/C/out/entrypoint.log 2>&1 &
```
`fuzz-entrypoint.sh` (ships with this skill) starts 1 main + N-1 secondary `afl-fuzz` instances, N = **80% of `nproc`** (override `FUZZ_CORES`),
with `-m none -t 1000+` and `AFL_AUTORESUME=1` (a restart continues the old state).
- `-t 1000+`: AFL++'s auto timeout (~20 ms) flags merely slow ASan runs as hangs; 1 s keeps real stalls only.
- The entrypoint renices itself to 19 (and IO class idle), so the fuzzers never compete with the user's desktop; renice running ones with
  `renice -n 19 -p $(pgrep -x afl-fuzz) $(pgrep -x harness)`. Run all side jobs (builds, replays, coverage) with `nice -n 19` too.
- Verify after ~30 s: `pgrep -c afl-fuzz` equals N, `V/C/out/*/fuzzer_stats` exist.
- **AFL++ on resume (`AFL_AUTORESUME`) renames the old `crashes/` and `hangs/` to `crashes.<date>` / `hangs.<date>`** and starts empty ones (nothing is deleted, `execs_done` and the saved_* counters reset).
  Every script, replay loop and copy-to-findings step must glob `*/crashes*/id:*` and `*/hangs*/id:*`, or earlier sessions' findings silently vanish from view.
- Stop with `pkill -TERM -x fuzz-entrypoint`, then wait until `pgrep -c afl-fuzz` is 0 before restarting (a start during shutdown ends with no instances running; always verify the count after the start). **Never `pkill -f <pattern>`** — a pattern like the harness path also matches
  the `afl-fuzz` command lines (and your own shell) and kills the campaign.

## 2b. Record session statistics (before ANY stop or restart)
The helper scripts below ship with this skill in `${CLAUDE_SKILL_DIR}/scripts/`. Run them from the workspace; they take the campaign id `<project>/<version>/<campaign>` and resolve paths against the current directory, or `$FUZZ_ROOT`.
AFL++ loses numbers on restart: `execs_done` resets on every resume, `run_time` counts from the original start (includes downtime), and CPU time is never kept.
`${CLAUDE_SKILL_DIR}/scripts/session-snapshot.py <out-dir> <campaign-dir> [checkpoint|final] [reason]` writes `<out-dir>/session.json` (segments, current checkpoint, cumulative
CPU hours, instance hours, execs, crashes/hangs, unique crash signatures, harness md5 + git commit, nice level, RAM, per-instance stats).
- The entrypoint runs it as `final` in its stop trap when launched with `FUZZ_SNAPSHOT=${CLAUDE_SKILL_DIR}/scripts/session-snapshot.py` exported (needs python3), the dashboard loop runs it as `checkpoint` every minute.
- Before a manual restart run it yourself: `python3 ${CLAUDE_SKILL_DIR}/scripts/session-snapshot.py V/C/out V/src/fuzz/C final "<why>"` — CPU time is read from /proc, so it must run while the processes still exist.
- A session that ended without a final snapshot is closed automatically from its last checkpoint at the next run.

## 2c. Run on a remote server instead (`remote-fuzz.sh`)
For a bigger box: `${CLAUDE_SKILL_DIR}/scripts/remote-fuzz.sh start <project>/<version>/<campaign> root@<host>` (from the workspace) does everything, idempotently, so it works for a fresh server, another campaign, or a resume:
- **Prepares the host:** installs `docker.io` + `rsync`, disables apport, persists `kernel.core_pattern=core` and `vm.mmap_rnd_bits=28` (ASan from clang < 18 dies at startup on the 32 bits newer kernels default to) in `/etc/sysctl.d/60-fuzz.conf`, sets the performance governor, and builds `fuzz-afl:ubuntu24.04` (Ubuntu 24.04 + distro AFL++ + python3). That's the toolchain the harness is built with here, so the **local harness runs unchanged** whatever the server's OS. It warns if the AFL++ versions differ. Extra runtime libraries go in `FUZZ_APT_PKGS`.
- **Syncs, in this order:** first it pulls whatever the server's `out/` already holds (so a missed sync never loses remote results), then it pushes `V/src/fuzz/C/` (harness + seeds), the entrypoint + snapshot script and the local `V/C/out/` to `/srv/fuzz/<project>-<version>-<campaign>/` (`FUZZ_REMOTE_DIR`). AFL then resumes on that state.
- **Smoke test:** before starting, it replays every seed inside the image. This catches missing libraries and the ASan/kernel problem above.
- **Runs** `nproc - 2` instances (`FUZZ_RESERVE_CORES`) in container `fuzz-<project>-<version>-<campaign>` as uid 1000, with `--restart unless-stopped` (survives reboots; `AFL_AUTORESUME`) and a memory cap at 90% of RAM. It checks the instance count after 45 s. Session snapshots are taken **in the container** (`FUZZ_CHECKPOINT=1`), because CPU time comes from the server's `/proc`.
- **Keeps local in sync:** it starts a local pull loop (`sync <campaign-id> <host> --loop`, every `FUZZ_SYNC_SECONDS`=60, pid in `V/work/remote-C.sync.pid`). Sync is **one way, server → local**, with `--delete` (AFL's `crashes.<date>` renames would otherwise leave stale copies). The local-only `dashboard.html`, `.triage.json` and `.remote.json` are protected. Each pull writes `V/C/out/.remote.json` (host, instance count, time), which `dashboard.py` and `status.sh` show instead of the local `pgrep`. Run `dashboard-loop.sh <campaign-id>` locally as usual: it renders and triages the synced copy, and its local checkpoints are no-ops.
- `status <campaign-id> <host>` shows the container, instance count, sync age and `afl-whatsup`. `stop <campaign-id> <host>` stops the local loop, then `docker stop` (the entrypoint's trap writes the final snapshot), then does a final pull. The server keeps its campaign dir; `start` resumes it.
- The sync loop is a local background process: it dies with the session container, while the server keeps fuzzing. Run `start` (it only re-attaches the loop when the container already runs) or `sync` to catch up.
- **Don't** run `afl-fuzz` locally on the same `V/C/out/` while the server owns it (`start` refuses), and don't push by hand. Never sync local → server while the container runs.
- Crash inputs end up on the server too. For embargoed findings, use only a box you control.

## 3. Dashboard every minute
`${CLAUDE_SKILL_DIR}/scripts/dashboard.py <campaign-id>` (run from the workspace) writes `V/C/out/dashboard.html` (auto-reload 60 s). Keep it fresh:
```bash
setsid nohup ${CLAUDE_SKILL_DIR}/scripts/dashboard-loop.sh <campaign-id> >/dev/null 2>&1 &
```
The dashboard shows both the **current AFL run** and the **campaign total across all sessions**: `session.json` (cumulative CPU hours, instance-hours, execs, a row per session,
explicitly listed *unrecorded* earlier sessions), archived rounds in `V/C/out/archive/roundN`, and the folders under `V/C/findings/`. Never extrapolate numbers that were not recorded.
Errors go to `V/work/dashboard.err`. Each crash is replayed once (cached in `V/C/out/.triage.json` by **content**, so the `crashes.<date>` renames on resume cost nothing). Each render spends at most `DASHBOARD_TRIAGE_SECONDS` (40) replaying and shows "N not replayed yet" for the rest, so the page stays fresh while a backlog drains.
`${CLAUDE_SKILL_DIR}/scripts/status.sh <campaign-id>` prints the same numbers plus crash signatures to the console.

## 4. Regular status reports in the console
Report on a regular schedule (e.g. a Monitor emitting `${CLAUDE_SKILL_DIR}/scripts/status.sh <campaign-id>` every 10 minutes, or whenever the user asks) and **say only real findings**:
instance count, total execs and execs/s, crash signatures (deduplicated by ASan SUMMARY line, with the top stack frames),
and anything that threatens the run (memory, dead instances). No speculation about where a bug "might" be.

## 5. Triage: crash, hang, or artefact?
- **Crash**: replay with symbols: `ASAN_OPTIONS=detect_leaks=0:symbolize=1 V/src/fuzz/C/harness < <file>`.
  Reproduces with an ASan report → candidate finding (confirm per 5b) (dedupe by SUMMARY + top frame). Does not reproduce / `sig:09` → OOM or
  environment, say so.
- **Hang** (`hangs/`): never trust the count. Replay each with a generous timeout and measure wall time (`date +%s%N`; `bc` and
  `/usr/bin/time` are not installed). Compare with the fuzzer's timeout:
  - far above it (seconds to minutes, e.g. 10 s+ vs 1 s) → real algorithmic slowness or infinite loop, **not** a timing artefact;
  - just above it, or fast when replayed alone → timing noise from the setup (load, timeout too low).
  Then locate it (`gdb -batch -ex "run < f" -ex bt`, interrupt by **pid**). Decide library vs harness: a hang that only occurs
  because the harness lets a huge declared size through (an input-size / element-count cap the harness should enforce) is a harness-config issue, otherwise the library.
- Check ASan/AFL settings first when something looks odd: `exec_timeout` in `fuzzer_stats`, stability (< ~90% = state leaks).

## 5b. Verify against a plain build (before calling anything a finding)
An ASan replay only shows the bug exists under the sanitizer. A candidate is only reported as a finding once it is checked
against a **plain build as it exists in the wild** (no ASan, no AFL++ instrumentation, `-O2`). Say explicitly which check was done.
- Build the library again with the stock compiler, same module/option set, no sanitizer, into `V/work/build-plain/`
  (keep a `V/triage/build-plain.sh` that mirrors the instrumented build's modules and flags). Run it at low priority while the fuzzer runs:
  `nice -n 19 make -j4`, so the fuzzing cores are not taken.
- Link a plain front end that compiles the campaign harness **unchanged** (`V/triage/plain.cc` shims the AFL++ macros and reads one
  input from stdin), so the same `process()` runs. No second harness to drift from the first.
- **Positive control first**: the plain front end must reproduce a known crasher in a plain *ASan-only* build before any negative result is
  believed. (A front end that took the buffer pointer before reading stdin processed an empty buffer and produced a bogus "clean" result.)
  Read the input in the equivalent of `__AFL_INIT()`, into an exact-size heap block.
- Run every candidate input three ways and record each: (1) plain binary alone (crash? wrong output? silent?),
  (2) plain binary under `valgrind` (needs `sudo apt-get update && sudo apt-get install valgrind`; session-only, propose adding it
  to the image) — this catches heap overflows without recompiling, (3) for hangs, wall time and peak RSS.
- Count **both** Valgrind classes: `Invalid read/write` and `uninitialised value` / `conditional jump depends on uninitialised`. ASan fills new heap
  with 0xBE and plain builds see zeroed pages, so an uninitialised-memory bug looks like an overflow under ASan and like nothing in a plain build
  (rerun with `--track-origins=yes` to find the allocation). Do not conclude "ASan artefact" before checking this.
- Interpretation: a 1-byte heap overflow is often silent in a plain build; "no visible crash" does not clear it, a Valgrind
  "Invalid read/write" does confirm it. Report ASan-only, plain-silent, plain-crash and Valgrind-confirmed separately.
- If ASan reproduces but plain + Valgrind are clean, the candidate is **unconfirmed**, not disproved: say so, and discriminate with a
  plain clang `-fsanitize=address`-only build (separates AFL++/LTO from ASan) and a `-O0` plain build. Valgrind's default red zones are 16 bytes;
  large-offset overflows can land in another live block, so also try `--redzone-size=2048`.
- Put the result next to the inputs in `V/C/findings/` (README table: which signature, ASan / plain / Valgrind outcome). Keep per signature the
  `minimized/` file plus **one** original (the one the minimised file or a PoC came from); the full set stays in `V/C/out/`.
  Triage tools and their results (`plain.cc`, dump tool, build scripts, Valgrind logs) go to `V/triage/` — they are evidence, not scratch.

## 5c. Minimise reproducers (one per signature, into `V/C/findings/minimized/`)
`afl-tmin` against the ASan harness (`ASAN_OPTIONS=…:abort_on_error=1`): needs `AFL_MAP_SIZE` (afl-tmin aborts "required size is very large" otherwise; read the real
value with `AFL_DEBUG=1 ./harness </dev/null 2>&1 | grep final_loc`, use the next power of two), `AFL_SKIP_CPUFREQ=1`, `AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1` and, for
slow inputs (huge declared sizes), `-t 20000` instead of the 1 s default. Run at `nice -n 19`. Format headers often leave little to shrink.
- **`afl-tmin` can change the bug class — always re-triage the minimised file.** Its keep/discard oracle only asks "does this still crash / keep the same edge coverage?", not "is it still the *same* ASan error at the *same* frame." When the root cause is a bad value used as an offset (uninitialised/overflowed index), the *symptom* — read vs write, overflow vs use-after-free vs SEGV — depends on heap layout and the geometry the input produces, which minimisation perturbs; so `tmin` can walk from one class to a neighbouring one (say, from a use-after-free to a heap-buffer-overflow) and report success. Re-run a **symbolised ASan replay on every minimised file and confirm the same error class at the same frame** before trusting or shipping it. A changed class at the same line is normally one root cause with a heap-dependent landing place — keep the **original un-minimised reproducer** for the class you actually mean, and label the minimised one by what it now does. To hold a class during minimisation, wrap the harness so the run only counts as "crash" when the ASan summary matches the target class/frame (dedup token), rather than on any fault.

Understand a minimised input by **dumping its structure** with a small tool that links the plain library and prints the parsed file (`V/triage/dump.cc`, using the project's own parser/printer the way its own dump utility would):
look for declared sizes that exceed the actual bytes, odd parameter values and format switches. That turns "crashes somewhere" into a testable statement.
To test a root-cause hypothesis, **instrument a private copy of the library** (a throwaway worktree: `git -C <project>/.fork worktree add --detach V/work/trace fuzzing/<version>`, add 1-2 `fprintf(stderr, …)` at the suspected spot,
build it at `-O0`, `nice -n 19 make -j2`, link the plain front end) and run the minimised inputs plus a normal seed as control; ~3 minutes and it replaces guessing.
Never edit `V/src/` for this; remove the trace worktree afterwards (`git -C <project>/.fork worktree remove V/work/trace`).
Check hypotheses on a sample of the saved hang/crash inputs with the dump tool and report the fraction that supports them, not just one example.

## 6. Fuzz past known findings (`patched/<version>`)
Once findings have fixes (`/fuzzing:vuln-report` commits one fix per root cause on the temporary branch `patched/<version>`), the fuzzer
must stop rediscovering them, or every new round is flooded with the same crashes and hangs:
- Stop the campaign (§2), take the final snapshot (§2b), and move the round aside: `mv V/C/out V/C/out.tmp && mkdir -p V/C/out/archive && mv V/C/out.tmp V/C/out/archive/roundN`
  (keep any older `archive/` rounds: move them back under the new `out/archive/`).
- Switch the worktree and rebuild **every** campaign of this version (they share the source): `git -C V/src switch patched/<version>`, then each `V/src/fuzz/*/build.sh`.
- Replay every saved finding of every campaign against the patched build: each must be gone. One that still fails means an incomplete fix — report it, do not fuzz on.
- Start the new round with the previous corpus as seeds, so coverage is not lost: `afl-cmin -i V/C/out/archive/roundN/main/queue -o V/work/C-cmin -- V/src/fuzz/C/harness`, then
  start as in §2 with `SEEDS=$PWD/V/work/C-cmin` added to the environment (the entrypoint uses `$CAMPAIGN/seeds` otherwise).
- New crashes on `patched/<version>` are new findings; a crash at a fixed site means the fix is incomplete.
The patch branch is temporary: on a new upstream release, `/fuzzing:fuzz-rebase` discards it and rebuilds it from the fixes that are still needed.

## 7. Optional: Docker image
`V/src/fuzz/C/Dockerfile` (build context = the campaign dir; the entrypoint comes from a named context) installs `afl++` on Ubuntu 24.04 and copies the prebuilt
harness, seeds and `fuzz-entrypoint.sh`. For a remote server, prefer §2c, which needs no per-campaign image. Docker may not exist in the session container; then give the user the commands instead of running them:
```bash
docker build -t fuzz-<project>-<version>-<campaign> --build-context tools=${CLAUDE_SKILL_DIR}/scripts V/src/fuzz/C
mkdir -p V/C/out
docker run -d --name fuzz-<project>-<version>-<campaign> --user "$(id -u):$(id -g)" -v "$PWD/V/C/out:/out" fuzz-<project>-<version>-<campaign>
```
Image tag, container name and host dir carry the campaign id, so campaigns run side by side. `docker logs -f <name>`, `docker stop <name>`.
The container shares the host's `core_pattern`. Build and run were not tested without Docker in the session; say so.
