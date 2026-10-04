# fuzzbar

Demo: an AI agent prepares a fuzzing campaign against a 3rd-party C/C++ library, AFL++
finds a bug in it, and the finding is replayed so the audience can see it happen.

## The three pieces

1. **Skill** (`.claude/skills/fuzz-campaign/`) — given a library, a version, and a goal
   (e.g. "fuzz the DICOM input parsing"), Claude builds the library with AFL++
   instrumentation and AddressSanitizer, writes a harness that reads one input from stdin,
   and generates a handful of seed inputs. Result: `campaigns/<name>/harness` +
   `campaigns/<name>/seeds/`.
2. **`afl-fuzz`**, run by hand on the host, fuzzes that one binary.
3. **Replay**: `campaigns/<name>/harness < <the input that triggered it>` reproduces the
   same finding outside AFL++, with a full AddressSanitizer report and stack trace.

There is no framework script in between — just the skill, the binary it produces, and
ordinary AFL++ commands. See `DEMO-RUNBOOK.md` for the exact commands, and
`demo-setup.sh` for host preparation before a live run.

## Worked example

DCMTK 3.6.3, the reference open-source toolkit for DICOM (the file format used by medical
imaging devices), carries CVE-2019-1010228: a buffer overflow in its RLE image decoder,
fixed in 3.6.4. From eight ordinary DICOM images and no hints about where the bug is,
fuzzing finds a crash in seconds on a single core. The crash is a real, documented,
already-fixed vulnerability — not a zero-day discovery.

## Requirements

Ubuntu 24.04 or similar. `demo-setup.sh` installs `afl++` and `gdb` if missing, and sets
`kernel.core_pattern=core` and the CPU governor to `performance`, which AFL++ requires
and which are not writable inside a container — this script is meant to run on the host.
