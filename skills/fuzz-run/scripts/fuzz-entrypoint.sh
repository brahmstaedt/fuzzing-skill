#!/bin/bash
# Runs one AFL++ main + (N-1) secondary instances sharing $OUT. N = 80% of nproc by default (override: FUZZ_CORES), at least 1.
# Ships with the fuzz-run skill; used natively, in the campaign's Docker image and by remote-fuzz.sh (as the container's command).
# Optional: DICT=<file> adds an AFL++ dictionary (-x). Secondaries cycle through different power schedules for diversity.
# -t: AFL++ would auto-pick ~20 ms, which flags merely slow ASan runs as hangs; 1 s (FUZZ_TIMEOUT, ms) leaves only real stalls.
# No "+": "-t 1000+" means "auto-calculate, 1000 is the maximum" and in practice produced timeouts of 25 s to 20 min.
set -u
renice -n 19 $$ >/dev/null 2>&1; command -v ionice >/dev/null && ionice -c3 -p $$ 2>/dev/null   # lowest CPU/IO priority: all afl-fuzz instances and harness children inherit it
CAMPAIGN=${CAMPAIGN:-/campaign}; OUT=${OUT:-/out}   # OUT/CAMPAIGN overridable so the same script runs outside docker
SEEDS=${SEEDS:-$CAMPAIGN/seeds}                     # e.g. the minimised corpus of the previous round when starting a new one
TMOUT=${FUZZ_TIMEOUT:-1000}                         # per-exec timeout in ms
N=${FUZZ_CORES:-$(( $(nproc) * 80 / 100 ))}; [ "$N" -lt 1 ] && N=1   # default: 80% of the cores
export ASAN_OPTIONS=detect_leaks=0:abort_on_error=1:symbolize=0:quarantine_size_mb=16:allocator_release_to_os_interval_ms=200   # leaks on error paths are not crashes; abort so AFL++ sees ASan reports
export AFL_NO_UI=1 AFL_SKIP_CPUFREQ=1 AFL_AUTORESUME=1             # no TTY; governor is the host's business; resume if $OUT already has state
mkdir -p $OUT
X=(); [ -n "${DICT:-}" ] && X=(-x "$DICT")
SCHED=(explore fast coe lin quad exploit rare)                      # AFL++ power schedules; the main keeps the default (fast)
pids=()
SNAP="${FUZZ_SNAPSHOT:-}"   # path to the skill's session-snapshot.py (records CPU hours/execs AFL++ loses on restart); set by the skill launch, skipped if unset/absent or no python3
# FUZZ_CHECKPOINT=1: also checkpoint every minute here. Needed where no dashboard loop runs next to the fuzzers (remote-fuzz.sh), since the snapshot reads CPU time from this machine's /proc.
cleanup() { [ -f "$SNAP" ] && command -v python3 >/dev/null && python3 "$SNAP" "$OUT" "$CAMPAIGN" final "${STOP_REASON:-stop}"; kill "${pids[@]}" 2>/dev/null; wait; exit 0; }
trap cleanup TERM INT
afl-fuzz -i "$SEEDS" -o $OUT -M main -m none -t "$TMOUT" "${X[@]}" -- "$CAMPAIGN/harness" >$OUT/main.log 2>&1 & pids+=($!)
for i in $(seq 1 $((N-1))); do
  p=${SCHED[$(( (i-1) % ${#SCHED[@]} ))]}
  afl-fuzz -i "$SEEDS" -o $OUT -S "s$i" -p $p -m none -t "$TMOUT" "${X[@]}" -- "$CAMPAIGN/harness" >$OUT/s$i.log 2>&1 & pids+=($!)
done
echo "started $N afl-fuzz instances (1 main + $((N-1)) secondary); results in $OUT/*/crashes"
while kill -0 "${pids[0]}" 2>/dev/null; do
  sleep 60 & wait $!   # backgrounded so a TERM runs the trap at once instead of after the sleep
  afl-whatsup -s $OUT 2>/dev/null | tail -n 12
  [ "${FUZZ_CHECKPOINT:-0}" = 1 ] && [ -f "$SNAP" ] && command -v python3 >/dev/null && python3 "$SNAP" "$OUT" "$CAMPAIGN" checkpoint minute >/dev/null
done
echo "main instance exited; see $OUT/main.log"; cleanup
